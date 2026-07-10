"""Storage version constants and migration framework (Storage Manager §2, §10)."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from engines.storage.atomic import atomic_write_json, read_json

logger = logging.getLogger("tubedub.storage.migration")

# Current storage schema version — bump when on-disk layout changes.
STORAGE_VERSION = 1

MigrationFn = Callable[[dict[str, Any], Path], dict[str, Any]]

# version -> migration function (migrates *from* version N to N+1)
_MIGRATIONS: dict[int, MigrationFn] = {}


def register_migration(from_version: int, fn: MigrationFn) -> None:
    """Register a migration that upgrades data from ``from_version`` to ``from_version + 1``."""
    _MIGRATIONS[from_version] = fn


def _migrate_v0_to_v1(data: dict[str, Any], project_dir: Path) -> dict[str, Any]:
    """Initial migration: legacy projects without ``storage_version``."""
    data.setdefault("storage_version", 1)
    data.setdefault("created_at", time.time())
    data.setdefault("updated_at", time.time())
    data.setdefault("last_opened_at", 0.0)
    data.setdefault("trashed", False)
    data.setdefault("trashed_at", 0.0)
    # Preserve legacy identifiers if present.
    if "task_id" in data and "legacy_task_id" not in data:
        data["legacy_task_id"] = data["task_id"]
    if "project_uuid" in data and "legacy_project_uuid" not in data:
        data["legacy_project_uuid"] = data["project_uuid"]
    return data


register_migration(0, _migrate_v0_to_v1)


def migrate_project_data(data: dict[str, Any], project_dir: Path) -> dict[str, Any]:
    """Run all pending migrations until ``data`` reaches :data:`STORAGE_VERSION`."""
    version = int(data.get("storage_version") or 0)
    if version > STORAGE_VERSION:
        logger.warning(
            "Project %s has future storage_version=%d (current=%d)",
            data.get("project_id"),
            version,
            STORAGE_VERSION,
        )
        return data
    while version < STORAGE_VERSION:
        fn = _MIGRATIONS.get(version)
        if fn is None:
            logger.error("No migration registered for version %d", version)
            break
        backup = project_dir / f".migration_backup_v{version}.json"
        try:
            atomic_write_json(backup, data)
        except OSError:
            pass
        data = fn(data, project_dir)
        version = int(data.get("storage_version") or version + 1)
        data["storage_version"] = version
        data["updated_at"] = time.time()
        logger.info(
            "Migrated project %s to storage_version=%d",
            data.get("project_id"),
            version,
        )
    return data


def migrate_legacy_studio_session(
    session_path: Path,
    project_dir: Path,
    *,
    project_id: str,
) -> dict[str, Any] | None:
    """Import a legacy ``output/studio_sessions/<id>.json`` into a v1 project record."""
    if not session_path.is_file():
        return None
    raw = read_json(session_path)
    if not isinstance(raw, dict):
        return None
    data: dict[str, Any] = {
        "project_id": project_id,
        "title": str(raw.get("title") or raw.get("video_name") or project_id)[:128],
        "storage_version": 0,
        "legacy_task_id": str(raw.get("session_id") or session_path.stem),
        "legacy_project_uuid": str(raw.get("project_uuid") or ""),
        "studio_session": raw,
        "created_at": float(raw.get("created_at") or time.time()),
        "updated_at": time.time(),
        "last_opened_at": 0.0,
        "trashed": False,
        "trashed_at": 0.0,
        "source": "legacy_studio_session",
        "source_path": str(session_path),
    }
    project_dir.mkdir(parents=True, exist_ok=True)
    return migrate_project_data(data, project_dir)


def migrate_legacy_tdproj(
    tdproj_path: Path,
    project_dir: Path,
    *,
    project_id: str,
) -> dict[str, Any] | None:
    """Import a legacy ``.tdproj`` file into a v1 project record."""
    raw = read_json(tdproj_path)
    if not isinstance(raw, dict):
        return None
    data: dict[str, Any] = {
        "project_id": project_id,
        "title": str(raw.get("title") or project_id)[:128],
        "storage_version": 0,
        "tdproj": raw,
        "legacy_task_id": str(raw.get("task_id") or ""),
        "legacy_project_uuid": str(raw.get("project_uuid") or ""),
        "created_at": float((raw.get("created_ms") or 0) / 1000) or time.time(),
        "updated_at": time.time(),
        "last_opened_at": 0.0,
        "trashed": False,
        "trashed_at": 0.0,
        "source": "legacy_tdproj",
        "source_path": str(tdproj_path),
    }
    project_dir.mkdir(parents=True, exist_ok=True)
    return migrate_project_data(data, project_dir)
