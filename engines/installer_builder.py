"""
Сборка Windows-установщика TubeDub (PyInstaller onedir + Inno Setup).
Пользователь получает один файл TubeDub_Setup.exe или TubeDub_Test_7_Days_Setup.exe.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
BUILDS_DIR = APP_DIR / "output" / "installers"
DIST_DIR = APP_DIR / "dist" / "TubeDub"
INSTALLER_WORK = APP_DIR / "output" / "_installer_work"

from engines.app_version import APP_VERSION
APP_PUBLISHER = "TubeDub"
EXE_NAME = "TubeDub.exe"
INNO_APP_ID_RETAIL = "A7F3B2C1-9D4E-4A8B-B5C3-1D2E3F4A5B6C"
INNO_APP_ID_TEST = "B8E4C3D2-8F5A-4B9C-A6D4-2E3F4A5B6C7"


def find_iscc() -> Path | None:
    env = os.environ.get("ISCC", "").strip()
    candidates = [
        Path(env) if env else None,
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for p in candidates:
        if p and p.is_file():
            return p
    for base in (
        Path(r"C:\Program Files (x86)"),
        Path(r"C:\Program Files"),
        Path.home() / "AppData" / "Local" / "Programs",
    ):
        if not base.is_dir():
            continue
        for p in base.glob("**/Inno Setup */ISCC.exe"):
            if p.is_file():
                return p
    which = shutil.which("ISCC")
    if which:
        return Path(which)
    return None


def _pyinstaller_available() -> bool:
    try:
        import PyInstaller  # noqa: F401

        return True
    except ImportError:
        return False


def _run_pyinstaller_onedir(source_dir: Path, dist_path: Path) -> tuple[bool, str]:
    import subprocess

    if not _pyinstaller_available():
        return False, "PyInstaller не установлен (pip install pyinstaller)"

    desktop_py = source_dir / "desktop.py"
    if not desktop_py.exists():
        return False, "desktop.py не найден"

    dist_path.mkdir(parents=True, exist_ok=True)
    work = INSTALLER_WORK / "pyi_work"
    spec_dir = INSTALLER_WORK / "pyi_spec"
    work.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    add_data = [
        ("templates", "templates"),
        ("static", "static"),
        ("styles", "styles"),
        ("data", "data"),
        ("engines", "engines"),
        ("api", "api"),
        ("modules", "modules"),
    ]
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "TubeDub",
        "--distpath",
        str(dist_path.parent),
        "--workpath",
        str(work),
        "--specpath",
        str(spec_dir),
    ]
    for folder, dest in add_data:
        src = source_dir / folder
        if src.exists():
            cmd.extend(["--add-data", f"{src}{os.pathsep}{dest}"])
    hidden = [
        "flask",
        "jinja2",
        "werkzeug",
        "edge_tts",
        "deep_translator",
        "langdetect",
        "faster_whisper",
        "pydub",
        "ffmpeg",
        "webview",
        "audioop",
        "engines.license_manager",
        "engines.dub_engine",
        "engines.dub_style_presets",
        "engines.dub_style_loader",
        "engines.voice_style_fx",
        "engines.stt_engine",
        "engines.tts",
    ]
    for h in hidden:
        cmd.extend(["--hidden-import", h])
    cmd.append(str(desktop_py))

    proc = subprocess.run(cmd, cwd=str(source_dir), capture_output=True, text=True, timeout=3600)
    exe = dist_path / EXE_NAME
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1200:]
        return False, f"PyInstaller: {tail}"
    if not exe.is_file():
        return False, "TubeDub.exe не создан после PyInstaller"
    return True, "ok"


def _bundle_language_models(payload_dir: Path) -> list[str]:
    """Копирует предустановленные языковые модели в payload установщика."""
    notes: list[str] = []
    src = APP_DIR / "models" / "huggingface"
    tubedub = payload_dir / "TubeDub"
    if not src.is_dir():
        notes.append(
            "models/huggingface не найден — установщик без предустановленных языковых пакетов. "
            "Запустите scripts/seed_bundled_language_packs.py перед сборкой."
        )
        return notes
    dest = tubedub / "models" / "huggingface"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    notes.append(f"Bundled language models from {src}")
    return notes


def _bundle_ffmpeg(payload_dir: Path) -> list[str]:
    """Копирует FFmpeg в payload, если найден локально."""
    notes: list[str] = []
    sources = [
        APP_DIR / "tools" / "ffmpeg",
        APP_DIR / "ffmpeg",
    ]
    for src in sources:
        if (src / "ffmpeg.exe").is_file():
            dest = payload_dir / "ffmpeg"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            notes.append(f"FFmpeg bundled from {src}")
            return notes
    notes.append(
        "FFmpeg не найден в tools/ffmpeg — установщик собран без FFmpeg. "
        "Добавьте ffmpeg.exe и ffprobe.exe в tools/ffmpeg перед сборкой."
    )
    return notes


def _write_inno_script(
    *,
    payload_dir: Path,
    output_dir: Path,
    setup_basename: str,
    app_id: str,
    display_name: str,
    is_test: bool,
) -> Path:
    iss_path = INSTALLER_WORK / f"{setup_basename}.iss"
    iss_path.parent.mkdir(parents=True, exist_ok=True)
    payload_win = str(payload_dir).replace("/", "\\")
    output_win = str(output_dir).replace("/", "\\")
    ffmpeg_files = ""
    ffmpeg_dir = payload_dir / "ffmpeg"
    if ffmpeg_dir.is_dir():
        ffmpeg_files = f"""
Source: "{payload_win}\\ffmpeg\\*"; DestDir: "{{app}}\\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs
"""

    test_note = " (тест 7 дней)" if is_test else ""
    iss = f"""
#define MyAppName "{display_name}"
#define MyAppVersion "{APP_VERSION}"
#define MyAppPublisher "{APP_PUBLISHER}"
#define MyAppExeName "{EXE_NAME}"

[Setup]
AppId={{{{{app_id}}}}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
DefaultDirName={{autopf}}\\TubeDub
DefaultGroupName=TubeDub
OutputDir={output_win}
OutputBaseFilename={setup_basename}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={{app}}\\{EXE_NAME}
DisableProgramGroupPage=no
DisableWelcomePage=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"; Flags: unchecked

[Files]
Source: "{payload_win}\\TubeDub\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
{ffmpeg_files}

[Icons]
Name: "{{group}}\\TubeDub"; Filename: "{{app}}\\{EXE_NAME}"; Comment: "TubeDub — автодубляж{test_note}"
Name: "{{userdesktop}}\\TubeDub"; Filename: "{{app}}\\{EXE_NAME}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{EXE_NAME}"; Description: "Запустить TubeDub"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{{localappdata}}\\TubeDub"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
"""
    iss_path.write_text(iss.strip() + "\n", encoding="utf-8")
    return iss_path


def _compile_inno(iss_path: Path) -> tuple[bool, str, Path | None]:
    iscc = find_iscc()
    if not iscc:
        return (
            False,
            "Inno Setup 6 не найден. Установите с https://jrsoftware.org/isinfo.php "
            "или задайте переменную ISCC=путь\\к\\ISCC.exe",
            None,
        )
    proc = subprocess.run(
        [str(iscc), str(iss_path)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1200:]
        return False, f"Inno Setup: {tail}", None
    out_dir = Path(iss_path.read_text(encoding="utf-8").split("OutputDir=")[1].split("\n")[0].strip())
    base = iss_path.stem
    setup = out_dir / f"{base}.exe"
    if not setup.is_file():
        return False, "Setup.exe не найден после компиляции", None
    return True, "ok", setup


def _disk_free_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(str(path))
        return usage.free / 1024**3
    except OSError:
        return -1.0


def create_setup_installer(
    *,
    test_build: bool = False,
    key_type: str = "TEST-7",
    label: str = "",
) -> dict[str, Any]:
    """
    test_build=False → TubeDub_Setup.exe (розница, auto-demo 7 дней при первом запуске)
    test_build=True  → TubeDub_Test_7_Days_Setup.exe (встроенная лицензия 7 дней)
    """
    from engines.test_build_manager import (
        KEY_TYPE_DAYS,
        _build_license_payload,
        _load_registry,
        _save_registry,
        _stage_project,
    )
    from engines.license_manager import generate_key, _register_key

    if test_build and key_type.upper() not in KEY_TYPE_DAYS:
        return {"ok": False, "error": f"Неизвестный тип: {key_type}"}

    free_gb = _disk_free_gb(APP_DIR)
    if 0 <= free_gb < 5.0:
        return {
            "ok": False,
            "error": f"Недостаточно места на диске ({free_gb:.1f} ГБ). Нужно минимум 5 ГБ для сборки установщика.",
        }

    build_id = uuid.uuid4().hex[:12]
    BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    staging = INSTALLER_WORK / f"staging_{build_id}"
    payload = INSTALLER_WORK / f"payload_{build_id}"
    key = None
    notes: list[str] = []

    setup_basename = "TubeDub_Test_7_Days_Setup" if test_build else "TubeDub_Setup"
    display_name = "TubeDub (тест 7 дней)" if test_build else "TubeDub"
    app_id = INNO_APP_ID_TEST if test_build else INNO_APP_ID_RETAIL

    try:
        _stage_project(staging)
        if test_build:
            key = generate_key(key_type)
            license_data = _build_license_payload(key, key_type.upper())
            (staging / "license.json").write_text(
                json.dumps(license_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        ok, msg = _run_pyinstaller_onedir(staging, payload / "TubeDub")
        if not ok:
            return {"ok": False, "error": msg}

        notes = _bundle_ffmpeg(payload)
        notes.extend(_bundle_language_models(payload))
        iss = _write_inno_script(
            payload_dir=payload,
            output_dir=BUILDS_DIR,
            setup_basename=setup_basename,
            app_id=app_id,
            display_name=display_name,
            is_test=test_build,
        )
        ok, msg, setup_exe = _compile_inno(iss)
        if not ok or not setup_exe:
            return {"ok": False, "error": msg, "notes": notes}

        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        stamped = BUILDS_DIR / f"{setup_basename}_{stamp}.exe"
        canonical = BUILDS_DIR / f"{setup_basename}.exe"
        setup_resolved = setup_exe.resolve()
        if setup_resolved != stamped.resolve():
            shutil.copy2(setup_exe, stamped)
        if setup_resolved != canonical.resolve():
            try:
                if canonical.is_file():
                    canonical.unlink()
                shutil.copy2(setup_exe, canonical)
            except PermissionError:
                logger.warning("Could not overwrite %s — using stamped build", canonical)
                canonical = stamped if stamped.is_file() else setup_exe
        else:
            canonical = setup_exe

    except Exception as e:
        logger.exception("Installer build failed: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(payload, ignore_errors=True)

    now = time.time()
    record: dict[str, Any] = {
        "id": build_id,
        "created_at": now,
        "format": "setup",
        "test_build": test_build,
        "label": label or "",
        "setup_path": str(canonical),
        "setup_stamped": str(stamped),
        "setup_name": canonical.name,
        "size_mb": round(
            (canonical if canonical.is_file() else stamped).stat().st_size / (1024 * 1024), 2
        ),
    }
    if test_build and key:
        record["key_type"] = key_type.upper()
        record["key"] = key.upper()
        record["expires_at"] = _build_license_payload(key, key_type.upper()).get("expires_at")
    else:
        record["key_type"] = "RETAIL"

    reg = _load_registry()
    reg.setdefault("builds", []).append(record)
    _save_registry(reg)

    if test_build and key:
        _register_key(
            key,
            {"type": key_type.upper(), "test_build_id": build_id, "created_at": now, "source": "setup_installer"},
        )

    return {
        "ok": True,
        "build_id": build_id,
        "setup_path": str(canonical),
        "setup_name": canonical.name,
        "setup_stamped": str(stamped),
        "size_mb": record["size_mb"],
        "notes": notes,
        "message": f"Установщик готов: {canonical.name} ({record['size_mb']} МБ)",
    }


def get_setup_path(build_id: str) -> Path | None:
    from engines.test_build_manager import _load_registry

    for b in _load_registry().get("builds", []):
        if b.get("id") == build_id:
            p = Path(b.get("setup_path", ""))
            return p if p.is_file() else None
    return None
