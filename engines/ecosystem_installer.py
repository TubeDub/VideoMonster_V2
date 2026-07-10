"""Install ecosystem standalone EXEs outside VideoMonster app folder."""

from __future__ import annotations

import logging
import os
import shutil
import struct
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = APP_DIR / "dist" / "ecosystem"
BUILD_SCRIPT = APP_DIR / "scripts" / "build_ecosystem.bat"
ICONS_DIR = APP_DIR / "data" / "ecosystem_icons"

APP_ENTRIES: tuple[tuple[str, str], ...] = (
    ("reader_app", "TubeDub Reader"),
    ("translator_app", "TubeDub Переводчик"),
    ("tts_app", "TubeDub Озвучка"),
    ("subtitle_studio_app", "TubeDub Студия субтитров"),
    ("srt_editor_app", "TubeDub SRT редактор"),
    ("quick_dub_app", "TubeDub Быстрый дубляж"),
    ("audio_reader_app", "TubeDub Аудио Reader"),
)


def get_install_dir() -> Path:
    custom = os.environ.get("VM_ECOSYSTEM_DIR", "").strip()
    if custom:
        return Path(custom)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        return Path(local) / "VideoMonsterFreeApps"
    return Path.home() / "VideoMonsterFreeApps"


def _expected_exe_names() -> list[str]:
    return [f"VM_{name}.exe" for name, _ in APP_ENTRIES]


def _dist_ready() -> bool:
    if not DIST_DIR.exists():
        return False
    return all((DIST_DIR / name).exists() for name in _expected_exe_names())


def run_build() -> tuple[bool, str]:
    if not BUILD_SCRIPT.exists():
        return False, "build_ecosystem.bat not found"
    logger.info("Building ecosystem EXEs via PyInstaller…")
    proc = subprocess.run(
        ["cmd", "/c", str(BUILD_SCRIPT)],
        cwd=str(APP_DIR),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        return False, f"build failed: {tail}"
    if not _dist_ready():
        return False, "build finished but EXE files missing in dist/ecosystem"
    return True, "build ok"


def _write_minimal_ico(path: Path, rgb: tuple[int, int, int] = (79, 142, 247)) -> None:
    """Минимальный 16×16 ICO без внешних зависимостей."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 16, 16
    pixels = bytes([rgb[2], rgb[1], rgb[0], 255] * (w * h))
    bmp_header = struct.pack("<IIIHHIIIIII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    mask_row = b"\x00" * ((w + 31) // 32 * 4)
    mask = mask_row * h
    image_data = bmp_header + pixels + mask
    ico = struct.pack("<HHH", 0, 1, 1)
    ico += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(image_data), 22)
    ico += image_data
    path.write_bytes(ico)


def ensure_icons() -> dict[str, Path]:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    palette = [
        (79, 142, 247),
        (52, 211, 153),
        (251, 191, 36),
        (244, 114, 182),
        (167, 139, 250),
        (248, 113, 113),
        (45, 212, 191),
    ]
    icons: dict[str, Path] = {}
    for i, (app_name, _label) in enumerate(APP_ENTRIES):
        ico = ICONS_DIR / f"VM_{app_name}.ico"
        if not ico.exists():
            _write_minimal_ico(ico, palette[i % len(palette)])
        icons[app_name] = ico
    return icons


def _desktop_dir() -> Path:
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        desktop = Path(userprofile) / "Desktop"
        if desktop.is_dir():
            return desktop
    return Path.home() / "Desktop"


def create_desktop_shortcuts(install_dir: Path | None = None) -> list[str]:
    """Создаёт ярлыки на рабочем столе (Windows). EXE остаются в LOCALAPPDATA."""
    install_dir = install_dir or get_install_dir()
    desktop = _desktop_dir()
    icons = ensure_icons()
    created: list[str] = []

    if os.name != "nt":
        logger.info("Desktop shortcuts skipped (not Windows)")
        return created

    for app_name, label in APP_ENTRIES:
        exe_name = f"VM_{app_name}.exe"
        exe_path = install_dir / exe_name
        if not exe_path.exists():
            continue
        lnk = desktop / f"{label}.lnk"
        icon = icons.get(app_name)
        ps = (
            f"$s=(New-Object -COM WScript.Shell).CreateShortcut('{lnk}');"
            f"$s.TargetPath='{exe_path}';"
            f"$s.WorkingDirectory='{install_dir}';"
            f"$s.Description='TubeDub Free App (VideoMonster Engine)';"
        )
        if icon and icon.exists():
            ps += f"$s.IconLocation='{icon}';"
        ps += "$s.Save()"
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            created.append(lnk.name)
        except Exception as e:
            logger.warning("Shortcut failed for %s: %s", exe_name, e)
    return created


def install_ecosystem(*, build_if_missing: bool = False) -> dict:
    install_dir = get_install_dir()
    install_dir.mkdir(parents=True, exist_ok=True)

    built = False
    build_message = ""
    if not _dist_ready():
        if build_if_missing:
            ok, msg = run_build()
            built = ok
            build_message = msg
            if not ok:
                logger.warning("Ecosystem build failed: %s", msg)
        else:
            logger.info("Ecosystem dist not ready — copy skipped (run build_ecosystem.bat)")

    copied: list[str] = []
    missing: list[str] = []
    for name in _expected_exe_names():
        src = DIST_DIR / name
        dst = install_dir / name
        if not src.exists():
            missing.append(name)
            continue
        if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
            shutil.copy2(src, dst)
            copied.append(name)

    shortcuts = create_desktop_shortcuts(install_dir) if not missing else []

    installed = sorted(p.name for p in install_dir.glob("VM_*.exe"))
    return {
        "ok": len(missing) == 0,
        "install_dir": str(install_dir),
        "dist_dir": str(DIST_DIR),
        "built": built,
        "build_message": build_message,
        "copied": copied,
        "installed": installed,
        "missing": missing,
        "shortcuts": shortcuts,
    }


def build_and_install_ecosystem() -> dict:
    """Полный цикл: сборка 7 EXE → установка → ярлыки."""
    result = install_ecosystem(build_if_missing=True)
    result["message"] = (
        f"Установлено {len(result.get('installed') or [])} приложений в "
        f"{result.get('install_dir')}"
    )
    if result.get("shortcuts"):
        result["message"] += f"; ярлыков: {len(result['shortcuts'])}"
    return result
