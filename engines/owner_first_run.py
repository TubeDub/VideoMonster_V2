"""
VideoMonster V2 — одноразовая инициализация при первом запуске владельца.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
PROJECTS_DIR = APP_DIR / "projects"
PENDING_MARKER = DATA_DIR / ".owner_first_run_pending"
INITIALIZED_MARKER = DATA_DIR / ".owner_initialized"
OWNER_SECRET = DATA_DIR / "license_secret.txt"
LICENSE_FILE = APP_DIR / "license.json"

REQUIRED_DIRS = [
    DATA_DIR,
    APP_DIR / "output",
    APP_DIR / "output" / "test_builds",
    APP_DIR / "uploads",
    PROJECTS_DIR,
]


def _dev_mode() -> bool:
    return os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def _is_test_build_copy() -> bool:
    if not LICENSE_FILE.exists():
        return False
    try:
        data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
        return bool(data.get("test_build"))
    except Exception:
        return False


def is_owner_host() -> bool:
    """Копия владельца (не тестовая сборка). Секрет может быть создан при первом run."""
    if _is_test_build_copy():
        return False
    if OWNER_SECRET.exists():
        return True
    # Мастер-копия без license.json или с pending-маркером
    if PENDING_MARKER.exists():
        return True
    if not LICENSE_FILE.exists() and not INITIALIZED_MARKER.exists():
        return True
    return False


def is_initialized() -> bool:
    return INITIALIZED_MARKER.exists()


def should_run(force: bool = False) -> bool:
    if force:
        return True
    if INITIALIZED_MARKER.exists() and not PENDING_MARKER.exists():
        return False
    return is_owner_host()


def _ensure_dirs() -> None:
    for d in REQUIRED_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def _ensure_owner_secret() -> None:
    if OWNER_SECRET.exists():
        return
    token = secrets.token_urlsafe(32)
    OWNER_SECRET.write_text(token, encoding="utf-8")
    logger.info("Created owner license secret: %s", OWNER_SECRET)


def _generate_sample_keys() -> list[str]:
    from engines.license_manager import generate_key

    keys: list[str] = []
    for key_type in ("TEST-7", "TEST-30"):
        key = generate_key(key_type)
        keys.append(key)
        logger.info("Generated sample key %s: %s", key_type, key)
    return keys


def _create_initial_test_build() -> dict | None:
    from engines.test_build_manager import create_test_build, create_test_exe_build

    results: list[dict] = []

    try:
        zip_result = create_test_build("TEST-7", label="Первый тестовый билд (авто-инициализация)")
        results.append(zip_result)
        if zip_result.get("ok"):
            logger.info("Initial test ZIP: %s", zip_result.get("zip_path"))
        else:
            logger.warning("Initial test ZIP failed: %s", zip_result.get("error"))
    except Exception as e:
        logger.exception("Initial test ZIP error: %s", e)
        zip_result = {"ok": False, "error": str(e)}

    exe_result: dict | None = None
    try:
        exe_result = create_test_exe_build("TEST-7", label="Первый EXE для Telegram")
        if exe_result.get("ok"):
            logger.info("Initial test EXE: %s", exe_result.get("exe_path"))
        else:
            logger.warning("Initial test EXE failed: %s", exe_result.get("error"))
    except Exception as e:
        logger.exception("Initial test EXE error: %s", e)
        exe_result = {"ok": False, "error": str(e)}

    ok = any(r.get("ok") for r in results) or (exe_result and exe_result.get("ok"))
    return {
        "ok": ok,
        "zip": zip_result,
        "exe": exe_result,
    }


def run_init(force: bool = False) -> tuple[bool, str]:
    """Выполнить полную инициализацию владельца."""
    if not should_run(force=force):
        return False, "Инициализация не требуется (уже выполнена или не владелец)"

    logger.info("Owner first-run initialization starting…")
    steps: list[str] = []

    _ensure_dirs()
    steps.append("директории")

    _ensure_owner_secret()
    steps.append("секрет лицензий")

    keys = _generate_sample_keys()
    steps.append(f"ключи ({len(keys)})")

    build = _create_initial_test_build()
    if build and build.get("ok"):
        parts = []
        if build.get("zip", {}).get("ok"):
            parts.append("ZIP")
        if build.get("exe", {}).get("ok"):
            parts.append("EXE")
        steps.append("тестовая сборка TEST-7 (" + "+".join(parts or ["частично"]) + ")")
    else:
        err = ""
        if build:
            err = build.get("zip", {}).get("error") or build.get("exe", {}).get("error") or ""
        steps.append(f"тестовая сборка (пропуск{': ' + err[:60] if err else ''})")

    try:
        from engines.ecosystem_installer import install_ecosystem

        eco = install_ecosystem(build_if_missing=True)
        if eco.get("installed") and len(eco["installed"]) >= 7:
            steps.append(f"экосистема ({len(eco['installed'])} exe)")
        elif eco.get("built") and eco.get("copied"):
            steps.append(f"экосистема (собрано, {len(eco.get('copied') or [])} скопировано)")
        elif eco.get("missing"):
            steps.append(f"экосистема (не хватает: {len(eco['missing'])} exe)")
        else:
            steps.append("экосистема (частично)")
        if eco.get("shortcuts"):
            steps.append(f"ярлыки ({len(eco['shortcuts'])})")
    except Exception as eco_err:
        logger.exception("Ecosystem install error: %s", eco_err)
        steps.append(f"экосистема (ошибка: {eco_err})")

    INITIALIZED_MARKER.write_text(
        f"initialized_at={time.time()}\n",
        encoding="utf-8",
    )
    if PENDING_MARKER.exists():
        PENDING_MARKER.unlink()
        steps.append("маркер pending удалён")

    msg = "Инициализация владельца: " + ", ".join(steps)
    logger.info(msg)
    return True, msg


def run_if_needed() -> dict:
    """Вызов при старте приложения."""
    if not should_run():
        return {"ran": False, "initialized": is_initialized(), "owner_host": is_owner_host()}

    try:
        ok, msg = run_init()
        return {"ran": True, "ok": ok, "message": msg, "initialized": is_initialized()}
    except Exception as e:
        logger.exception("Owner first-run failed: %s", e)
        return {
            "ran": True,
            "ok": False,
            "message": str(e),
            "initialized": is_initialized(),
        }


def mark_pending_for_next_run() -> None:
    """Пометить для повторной инициализации (dev / сброс)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_MARKER.write_text(f"pending_at={time.time()}\n", encoding="utf-8")
    if INITIALIZED_MARKER.exists():
        INITIALIZED_MARKER.unlink()
