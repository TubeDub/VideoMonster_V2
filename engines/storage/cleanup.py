"""Storage cleanup (Storage Manager §9).

Удаляет:
  * временные файлы (``.writing``, ``.tmp``, ``.partial``);
  * протухшие lock-файлы (владелец-процесс мёртв);
  * незавершённые атомарные записи;
  * старые кэши (делегирует в ``engines.storage_cleanup``).

Никогда не удаляет:
  * ``project.json`` активных проектов;
  * финальные MP4/MP3;
  * каталоги проектов в корзине (только по явному ``empty_trash``).
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.storage.atomic import TEMP_SUFFIXES
from engines.storage.locks import _pid_alive

logger = logging.getLogger("tubedub.storage.cleanup")


@dataclass
class CleanupReport:
    files_deleted: int = 0
    bytes_freed: int = 0
    stale_locks_removed: int = 0
    temp_files_removed: int = 0
    cache_bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_deleted": self.files_deleted,
            "bytes_freed": self.bytes_freed,
            "stale_locks_removed": self.stale_locks_removed,
            "temp_files_removed": self.temp_files_removed,
            "cache_bytes_freed": self.cache_bytes_freed,
            "errors": list(self.errors),
        }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def cleanup_temp_files(
    root: Path,
    *,
    max_age_sec: float = 3600,
    report: CleanupReport | None = None,
) -> CleanupReport:
    """Remove incomplete temp files older than ``max_age_sec`` under ``root``."""
    rep = report or CleanupReport()
    if not root.is_dir():
        return rep
    now = time.time()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if not any(name.endswith(s) or s in name for s in TEMP_SUFFIXES):
            continue
        try:
            age = now - path.stat().st_mtime
            if age < max_age_sec:
                continue
            size = _file_size(path)
            path.unlink(missing_ok=True)
            rep.temp_files_removed += 1
            rep.files_deleted += 1
            rep.bytes_freed += size
        except OSError as exc:
            rep.errors.append(f"{path}: {exc}")
    return rep


def cleanup_stale_locks(locks_dir: Path, report: CleanupReport | None = None) -> CleanupReport:
    """Remove lock files whose owner process is no longer alive."""
    rep = report or CleanupReport()
    if not locks_dir.is_dir():
        return rep
    import json

    for lock_file in locks_dir.glob("*.lock"):
        try:
            info = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = int(info.get("pid") or 0)
            if pid and not _pid_alive(pid):
                size = _file_size(lock_file)
                lock_file.unlink(missing_ok=True)
                rep.stale_locks_removed += 1
                rep.files_deleted += 1
                rep.bytes_freed += size
        except (OSError, ValueError):
            try:
                lock_file.unlink(missing_ok=True)
                rep.stale_locks_removed += 1
            except OSError:
                pass
    return rep


def cleanup_migration_backups(project_dir: Path, report: CleanupReport | None = None) -> CleanupReport:
    """Remove old ``.migration_backup_v*.json`` files (keep newest)."""
    rep = report or CleanupReport()
    backups = sorted(project_dir.glob(".migration_backup_v*.json"))
    for old in backups[:-1]:
        try:
            size = _file_size(old)
            old.unlink(missing_ok=True)
            rep.files_deleted += 1
            rep.bytes_freed += size
        except OSError as exc:
            rep.errors.append(str(exc))
    return rep


def run_storage_cleanup(
    app_dir: Path,
    *,
    include_pipeline_cache: bool = True,
) -> CleanupReport:
    """Full storage cleanup pass — safe to run at startup."""
    from engines.storage.paths import StoragePaths

    paths = StoragePaths.resolve(app_dir)
    report = CleanupReport()

    # Temp / incomplete writes under projects + output.
    cleanup_temp_files(paths.projects_root, report=report)
    cleanup_temp_files(paths.trash_root, report=report)
    cleanup_temp_files(paths.output_dir, report=report)
    cleanup_stale_locks(paths.locks_dir, report=report)

    for project_dir in paths.projects_root.iterdir():
        if project_dir.is_dir():
            cleanup_migration_backups(project_dir, report=report)

    if include_pipeline_cache:
        try:
            from engines.storage_cleanup import cleanup_pipeline_temp

            cache_report = cleanup_pipeline_temp(app_dir)
            report.cache_bytes_freed = int(getattr(cache_report, "bytes_freed", 0) or 0)
            report.bytes_freed += report.cache_bytes_freed
        except Exception as exc:
            report.errors.append(f"pipeline_cache: {exc}")

    logger.info(
        "Storage cleanup: deleted=%d freed=%d stale_locks=%d temp=%d",
        report.files_deleted,
        report.bytes_freed,
        report.stale_locks_removed,
        report.temp_files_removed,
    )
    return report
