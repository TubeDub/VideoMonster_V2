"""Module readiness — GREEN / YELLOW / RED status per module (TZ §16)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

_STATUS_MAP = {
    "stable": "GREEN",
    "beta": "YELLOW",
    "development": "RED",
    "disabled": "RED",
}


class ModuleStatus(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


def _app_dir(app_dir: Path | None = None) -> Path:
    return Path(app_dir or Path(__file__).resolve().parents[2])


def get_module_status(module_id: str, app_dir: Path | None = None) -> ModuleStatus:
    from engines.module_registry.registry import get_registry

    rec = get_registry(_app_dir(app_dir)).get(module_id)
    if not rec:
        return ModuleStatus.RED
    key = _STATUS_MAP.get(rec.status, "RED")
    return ModuleStatus(key)


def is_module_green(module_id: str, app_dir: Path | None = None) -> bool:
    return get_module_status(module_id, app_dir) == ModuleStatus.GREEN


def readiness_table(app_dir: Path | None = None) -> list[dict[str, Any]]:
    """Full GREEN/YELLOW/RED table for docs and dev UI."""
    from engines.module_registry.registry import get_registry

    rows: list[dict[str, Any]] = []
    for rec in get_registry(_app_dir(app_dir)).all_modules():
        status = get_module_status(rec.id, app_dir)
        rows.append(
            {
                "id": rec.id,
                "label": rec.label("ru"),
                "route": rec.route,
                "registry_status": rec.status,
                "readiness": status.value,
                "visible_to_users": rec.visible_to_users,
                "developer_only": rec.developer_only,
                "feature_id": rec.feature_id,
            }
        )
    return rows
