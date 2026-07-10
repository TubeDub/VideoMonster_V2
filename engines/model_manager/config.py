"""ModelManager settings — data/model_manager.json"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_MAX_GB = 10.0
CONFIG_FILE = "model_manager.json"


def config_path(app_dir: Path) -> Path:
    return app_dir / "data" / CONFIG_FILE


def load_config(app_dir: Path) -> dict[str, Any]:
    path = config_path(app_dir)
    default = {
        "version": 1,
        "storage_root": "",
        "max_storage_gb": DEFAULT_MAX_GB,
        "lru_enabled": True,
        "require_confirm_before_lru": True,
        "cleanup_unused_days": 90,
        "last_cleanup": "",
        "storage_wizard_done": False,
    }
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {**default, **data} if isinstance(data, dict) else default
    except Exception:
        return default


def mark_storage_wizard_done(app_dir: Path) -> None:
    cfg = load_config(app_dir)
    cfg["storage_wizard_done"] = True
    save_config(app_dir, cfg)


def needs_storage_wizard(app_dir: Path) -> bool:
    cfg = load_config(app_dir)
    return not bool(cfg.get("storage_wizard_done", False))


def save_config(app_dir: Path, data: dict[str, Any]) -> None:
    path = config_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
