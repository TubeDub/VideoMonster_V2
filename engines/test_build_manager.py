"""
TubeDub — тестовые сборки с встроенной лицензией для распространения.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
REGISTRY_FILE = DATA_DIR / "test_builds_registry.json"
BUILDS_DIR = APP_DIR / "output" / "test_builds"
TESTER_README_NAME = "README_ДЛЯ_ТЕСТЕРА.txt"

EXCLUDE_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".cursor",
        ".vscode",
    }
)

EXCLUDE_TOP_LEVEL = frozenset({"output", "uploads", "projects"})

OWNER_ONLY_REL = frozenset(
    {
        "data/license_secret.txt",
        "data/license_registry.json",
        "data/license_revoked.json",
        "data/test_builds_registry.json",
        "data/license_server_db.json",
        "data/.owner_initialized",
        "data/.owner_first_run_pending",
        "license.json",
    }
)

KEY_TYPE_DAYS: dict[str, int | None] = {
    "TEST-7": 7,
    "TEST-30": 30,
    "PREMIUM-WEEK": 7,
    "PREMIUM-MONTH": 30,
    "PREMIUM-YEAR": 365,
    "LIFETIME": None,
}

KEY_TYPE_TIER: dict[str, str] = {
    "TEST-7": "demo",
    "TEST-30": "demo",
    "PREMIUM-WEEK": "premium",
    "PREMIUM-MONTH": "premium",
    "PREMIUM-YEAR": "premium",
    "LIFETIME": "premium",
}

TESTER_README_TEMPLATE = """TubeDub — тестовая сборка
(Powered by VideoMonster Engine)
================================

Это ТЕСТОВАЯ версия для проверки программы. Срок действия ограничен.

ЗАПУСК
------
1. Установите Python 3.10+ (https://python.org) с галочкой «Add to PATH».
2. Установите FFmpeg и добавьте в PATH (https://ffmpeg.org).
3. Дважды щёлкните install_and_run.bat
   или: pip install -r requirements_desktop.txt && python desktop.py

ЛИЦЕНЗИЯ
--------
Тип: {key_type}
Ключ: {key}
Срок: {duration_text}

Лицензия уже активирована в этой сборке.
После истечения срока программа НЕ удаляется — остаются базовые функции
(Reader, ручной дубляж, 15 переводов в день). Premium-функции отключаются.

Для продления обратитесь к владельцу TubeDub.

ВАЖНО
-----
• Нужен интернет для озвучки (Edge-TTS) и онлайн-перевода.
• Не распространяйте сборку — только для личного теста.
"""


def _load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        return {"builds": []}
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Registry read failed: %s", e)
        return {"builds": []}


def _save_registry(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _build_license_payload(key: str, key_type: str) -> dict:
    days = KEY_TYPE_DAYS.get(key_type)
    tier = KEY_TYPE_TIER.get(key_type, "demo")
    now = time.time()
    expires = None if days is None else now + days * 86400
    return {
        "tier": tier,
        "key": key.upper(),
        "key_type": key_type,
        "activated_at": now,
        "expires_at": expires,
        "device_id": "",
        "last_sync_at": now,
        "auto_demo": False,
        "revoked": False,
        "test_build": True,
        "usage": {"translate_count": 0, "translate_day": _today()},
    }


def _duration_text(key_type: str) -> str:
    days = KEY_TYPE_DAYS.get(key_type)
    if days is None:
        return "бессрочно (Premium)"
    return f"{days} дней"


def _should_skip_path(rel: str) -> bool:
    rel_norm = rel.replace("\\", "/")
    if rel_norm in OWNER_ONLY_REL:
        return True
    parts = Path(rel_norm).parts
    if parts and parts[0] in EXCLUDE_TOP_LEVEL:
        return True
    for p in parts:
        if p in EXCLUDE_DIR_NAMES:
            return True
    return False


def _clean_owner_files(staging: Path) -> None:
    for rel in OWNER_ONLY_REL:
        p = staging / rel
        if p.is_file():
            p.unlink()


def _stage_project(staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    for item in APP_DIR.iterdir():
        if item.name in EXCLUDE_DIR_NAMES or item.name in EXCLUDE_TOP_LEVEL:
            continue
        dest = staging / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                dest,
                ignore=shutil.ignore_patterns(*EXCLUDE_DIR_NAMES),
                dirs_exist_ok=True,
            )
        else:
            shutil.copy2(item, dest)

    data_staging = staging / "data"
    data_staging.mkdir(parents=True, exist_ok=True)
    # keep license_server.json template if exists
    src_srv = DATA_DIR / "license_server.json"
    if src_srv.exists():
        shutil.copy2(src_srv, data_staging / "license_server.json")
    _clean_owner_files(staging)


def _write_test_artifacts(staging: Path, key: str, key_type: str) -> None:
    license_data = _build_license_payload(key, key_type)
    license_path = staging / "license.json"
    license_path.write_text(json.dumps(license_data, ensure_ascii=False, indent=2), encoding="utf-8")

    readme = TESTER_README_TEMPLATE.format(
        key_type=key_type,
        key=key.upper(),
        duration_text=_duration_text(key_type),
    )
    (staging / TESTER_README_NAME).write_text(readme, encoding="utf-8")


def _make_zip(staging: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(staging):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES]
            for fname in files:
                full = Path(root) / fname
                rel = full.relative_to(staging).as_posix()
                if _should_skip_path(rel):
                    continue
                zf.write(full, arcname=rel)


def create_test_build(key_type: str, label: str = "") -> dict[str, Any]:
    """Создать ZIP тестовой сборки с встроенной лицензией."""
    key_type = key_type.upper().strip()
    if key_type not in KEY_TYPE_DAYS:
        return {"ok": False, "error": f"Неизвестный тип: {key_type}"}

    from engines.license_manager import _register_key, generate_key

    key = generate_key(key_type)
    build_id = uuid.uuid4().hex[:12]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    zip_name = f"TubeDub_V2_{key_type}_{stamp}.zip"
    BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BUILDS_DIR / zip_name
    staging = BUILDS_DIR / f"_staging_{build_id}"

    try:
        _stage_project(staging)
        _write_test_artifacts(staging, key, key_type)
        _make_zip(staging, zip_path)
    except Exception as e:
        logger.exception("Test build failed: %s", e)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": str(e)}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    license_data = _build_license_payload(key, key_type)
    now = time.time()
    record = {
        "id": build_id,
        "created_at": now,
        "key_type": key_type,
        "key": key.upper(),
        "label": label or "",
        "zip_path": str(zip_path),
        "zip_name": zip_name,
        "expires_at": license_data.get("expires_at"),
        "revoked": False,
        "tier": license_data["tier"],
    }

    reg = _load_registry()
    reg.setdefault("builds", []).append(record)
    _save_registry(reg)

    _register_key(
        key,
        {
            "type": key_type,
            "test_build_id": build_id,
            "created_at": now,
            "source": "test_build",
        },
    )

    size_mb = round(zip_path.stat().st_size / (1024 * 1024), 2)
    return {
        "ok": True,
        "build_id": build_id,
        "key": key.upper(),
        "key_type": key_type,
        "zip_path": str(zip_path),
        "zip_name": zip_name,
        "size_mb": size_mb,
        "expires_at": record["expires_at"],
        "message": f"Сборка создана: {zip_name} ({size_mb} МБ)",
    }


def list_test_builds() -> list[dict]:
    reg = _load_registry()
    builds = reg.get("builds", [])
    out = []
    for b in sorted(builds, key=lambda x: x.get("created_at", 0), reverse=True):
        zip_path = Path(b.get("zip_path", ""))
        exe_path = Path(b.get("exe_path", ""))
        out.append(
            {
                **b,
                "zip_exists": zip_path.is_file(),
                "exe_exists": exe_path.is_file(),
                "setup_exists": Path(b.get("setup_path", "")).is_file(),
                "duration_text": _duration_text(b.get("key_type", "TEST-7")),
            }
        )
    return out


def revoke_test_license(key: str) -> tuple[bool, str]:
    from engines.license_manager import revoke_key

    key = key.strip().upper()
    if not key:
        return False, "Ключ не указан"

    revoke_key(key)
    reg = _load_registry()
    found = False
    for b in reg.get("builds", []):
        if b.get("key", "").upper() == key:
            b["revoked"] = True
            b["revoked_at"] = time.time()
            found = True
    _save_registry(reg)

    msg = f"Ключ {key} отключён"
    if not found:
        msg += " (не найден в реестре сборок, но добавлен в revoked)"
    return True, msg


def extend_test_license(key: str, days: int | None = None, lifetime: bool = False) -> tuple[bool, str]:
    from engines.license_manager import REGISTRY_FILE, _load_json, _save_json

    key = key.strip().upper()
    reg = _load_registry()
    rec = None
    for b in reg.get("builds", []):
        if b.get("key", "").upper() == key:
            rec = b
            break

    if not rec:
        return False, "Ключ не найден в реестре тестовых сборок"

    now = time.time()
    if lifetime:
        rec["expires_at"] = None
        rec["tier"] = "premium"
        rec["key_type"] = "LIFETIME"
        msg = f"Ключ {key}: Premium бессрочно"
    elif days is not None:
        base = max(now, float(rec.get("expires_at") or now))
        rec["expires_at"] = base + int(days) * 86400
        msg = f"Ключ {key}: +{days} дн."
    else:
        return False, "Укажите days или lifetime"

    rec["extended_at"] = now
    _save_registry(reg)

    lic_reg = _load_json(REGISTRY_FILE, {"keys": {}})
    if key in lic_reg.get("keys", {}):
        lic_reg["keys"][key]["extended_at"] = now
        if lifetime:
            lic_reg["keys"][key]["type"] = "LIFETIME"
        _save_json(REGISTRY_FILE, lic_reg)

    return True, msg


def get_build_zip_path(build_id: str) -> Path | None:
    for b in _load_registry().get("builds", []):
        if b.get("id") == build_id:
            p = Path(b.get("zip_path", ""))
            return p if p.is_file() else None
    return None


def _pyinstaller_available() -> bool:
    try:
        import PyInstaller  # noqa: F401

        return True
    except ImportError:
        return False


def _run_pyinstaller_test_exe(staging: Path, exe_out_dir: Path) -> tuple[bool, str, Path | None]:
    """Собирает TubeDub_Test_7_Days.exe из staging через PyInstaller."""
    import subprocess

    if not _pyinstaller_available():
        return False, "PyInstaller не установлен (pip install pyinstaller)", None

    desktop_py = staging / "desktop.py"
    if not desktop_py.exists():
        return False, "desktop.py отсутствует в staging", None

    exe_out_dir.mkdir(parents=True, exist_ok=True)
    work = BUILDS_DIR / "_pyi_work"
    spec_dir = BUILDS_DIR / "_pyi_spec"
    work.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    name = "TubeDub_Test_7_Days"
    add_data = [
        ("templates", "templates"),
        ("static", "static"),
        ("data", "data"),
        ("engines", "engines"),
        ("api", "api"),
        ("modules", "modules"),
    ]
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        name,
        "--distpath",
        str(exe_out_dir),
        "--workpath",
        str(work),
        "--specpath",
        str(spec_dir),
    ]
    for src, dest in add_data:
        src_path = staging / src
        if src_path.exists():
            cmd.extend(["--add-data", f"{src_path}{os.pathsep}{dest}"])
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
        "engines.license_manager",
        "engines.dub_engine",
        "engines.stt_engine",
        "engines.tts",
    ]
    for h in hidden:
        cmd.extend(["--hidden-import", h])
    cmd.append(str(desktop_py))

    logger.info("PyInstaller test EXE: %s", " ".join(cmd[:8]) + " …")
    proc = subprocess.run(
        cmd,
        cwd=str(staging),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    exe_path = exe_out_dir / f"{name}.exe"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        return False, f"PyInstaller failed: {tail}", None
    if not exe_path.is_file():
        return False, "EXE не найден после сборки", None
    return True, "ok", exe_path


def create_test_exe_build(key_type: str = "TEST-7", label: str = "") -> dict[str, Any]:
    """Создать EXE тестовой сборки с встроенной лицензией (для Telegram)."""
    key_type = key_type.upper().strip()
    if key_type not in KEY_TYPE_DAYS:
        return {"ok": False, "error": f"Неизвестный тип: {key_type}"}

    from engines.license_manager import _register_key, generate_key

    key = generate_key(key_type)
    build_id = uuid.uuid4().hex[:12]
    BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    staging = BUILDS_DIR / f"_staging_exe_{build_id}"
    exe_dir = BUILDS_DIR / "exe"
    final_name = "TubeDub_Test_7_Days.exe"
    if key_type != "TEST-7":
        final_name = f"TubeDub_{key_type}.exe"

    try:
        _stage_project(staging)
        _write_test_artifacts(staging, key, key_type)
        ok, msg, built_exe = _run_pyinstaller_test_exe(staging, exe_dir)
        if not ok or not built_exe:
            return {"ok": False, "error": msg}

        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        stamped = BUILDS_DIR / f"TubeDub_{key_type}_{stamp}.exe"
        shutil.copy2(built_exe, stamped)
        canonical = BUILDS_DIR / final_name
        shutil.copy2(built_exe, canonical)
    except Exception as e:
        logger.exception("Test EXE build failed: %s", e)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": str(e)}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    license_data = _build_license_payload(key, key_type)
    now = time.time()
    record = {
        "id": build_id,
        "created_at": now,
        "key_type": key_type,
        "key": key.upper(),
        "label": label or "",
        "exe_path": str(canonical),
        "exe_stamped": str(stamped),
        "format": "exe",
        "expires_at": license_data.get("expires_at"),
        "revoked": False,
        "tier": license_data["tier"],
    }

    reg = _load_registry()
    reg.setdefault("builds", []).append(record)
    _save_registry(reg)

    _register_key(
        key,
        {
            "type": key_type,
            "test_build_id": build_id,
            "created_at": now,
            "source": "test_build_exe",
        },
    )

    size_mb = round(canonical.stat().st_size / (1024 * 1024), 2)
    return {
        "ok": True,
        "build_id": build_id,
        "key": key.upper(),
        "key_type": key_type,
        "exe_path": str(canonical),
        "exe_name": canonical.name,
        "exe_stamped": str(stamped),
        "size_mb": size_mb,
        "expires_at": record["expires_at"],
        "message": f"EXE создан: {canonical.name} ({size_mb} МБ) — готов для Telegram",
    }
