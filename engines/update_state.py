"""Сохранённое состояние проверки обновлений (без обращения к серверу при запуске)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.app_version import APP_VERSION


def _state_path(app_dir: Path) -> Path:
    return app_dir / "output" / "update_state.json"


def load_update_state(app_dir: Path) -> dict[str, Any]:
    path = _state_path(app_dir)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("installed_version", APP_VERSION)
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "installed_version": APP_VERSION,
        "latest_version": "",
        "update_available": False,
        "download_url": "",
        "notes": "",
        "last_checked_at": "",
        "last_check_ok": False,
        "last_error": "",
    }


def save_update_state(app_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    path = _state_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def record_check_result(app_dir: Path, check: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    state = load_update_state(app_dir)
    state["installed_version"] = check.get("current") or APP_VERSION
    state["last_checked_at"] = now

    if not check.get("ok"):
        state["last_check_ok"] = False
        state["last_error"] = check.get("error") or "check failed"
        return save_update_state(app_dir, state)

    state["last_check_ok"] = True
    state["last_error"] = ""
    state["latest_version"] = check.get("latest") or ""
    state["update_available"] = bool(check.get("update_available"))
    state["download_url"] = check.get("download_url") or ""
    state["notes"] = check.get("notes") or ""
    return save_update_state(app_dir, state)


def record_apply_started(app_dir: Path) -> dict[str, Any]:
    state = load_update_state(app_dir)
    state["update_pending_install"] = True
    state["update_pending_at"] = datetime.now(timezone.utc).isoformat()
    return save_update_state(app_dir, state)


def clear_pending_update(app_dir: Path) -> dict[str, Any]:
    """После успешной установки новой версии — сброс флага обновления."""
    state = load_update_state(app_dir)
    state["installed_version"] = APP_VERSION
    if state.get("latest_version") and state["latest_version"] == APP_VERSION:
        state["update_available"] = False
    state["update_pending_install"] = False
    return save_update_state(app_dir, state)
