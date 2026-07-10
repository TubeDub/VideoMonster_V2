"""StorageManager — единая точка доступа к проектам (Storage Manager §1, §11)."""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

from engines.storage.atomic import atomic_write_json, read_json
from engines.storage.cleanup import run_storage_cleanup
from engines.storage.events import StorageEvent, StorageEventBus
from engines.storage.locks import (
    ProjectFileLock,
    StorageLockError,
    is_locked,
    project_thread_lock,
    project_write_lock,
)
from engines.storage.migration import (
    STORAGE_VERSION,
    migrate_legacy_studio_session,
    migrate_legacy_tdproj,
    migrate_project_data,
)
from engines.storage.model import ProjectRecord
from engines.storage.paths import StoragePaths
from engines.storage.recovery import (
    check_recovery,
    clear_recovery_state,
    save_recovery_state,
)

logger = logging.getLogger("tubedub.storage.manager")

_OPEN_SESSIONS: dict[str, dict[str, Any]] = {}
_MANAGER_LOCK = threading.RLock()
_MANAGERS: dict[str, "StorageManager"] = {}


class StorageManager:
    """Centralized project storage — the only supported access point.

    All project CRUD, trash, export/import, statistics, recovery and cleanup
    go through this class. Direct filesystem access to ``project.json`` from
    other modules is discouraged and will be migrated incrementally.
    """

    def __init__(self, app_dir: str | Path):
        self.paths = StoragePaths.resolve(app_dir)
        self.paths.ensure_dirs()
        self.events = StorageEventBus(journal_path=self.paths.events_journal)
        self._active_index: dict[str, dict[str, Any]] = {}
        self._trash_index: dict[str, dict[str, Any]] = {}
        self._load_indexes()

    # ── Index management ──────────────────────────────────────────────

    def _load_indexes(self) -> None:
        self._active_index = dict(
            (read_json(self.paths.index_path) or {}).get("projects") or {}
        )
        self._trash_index = dict(
            (read_json(self.paths.trash_index_path) or {}).get("projects") or {}
        )

    def _save_active_index(self) -> None:
        atomic_write_json(
            self.paths.index_path,
            {"storage_version": STORAGE_VERSION, "projects": self._active_index},
        )

    def _save_trash_index(self) -> None:
        atomic_write_json(
            self.paths.trash_index_path,
            {"storage_version": STORAGE_VERSION, "projects": self._trash_index},
        )

    def _index_row(self, record: ProjectRecord, project_dir: Path) -> dict[str, Any]:
        return {
            "project_id": record.project_id,
            "title": record.title,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "last_opened_at": record.last_opened_at,
            "size_bytes": record.disk_size_bytes(project_dir),
            "storage_version": record.storage_version,
            "legacy_task_id": record.legacy_task_id,
        }

    # ── Internal I/O ──────────────────────────────────────────────────

    def _read_project_file(
        self, project_id: str, *, trashed: bool = False
    ) -> ProjectRecord | None:
        path = self.paths.project_json(project_id, trashed=trashed)
        data = read_json(path)
        if not isinstance(data, dict):
            return None
        project_dir = self.paths.project_dir(project_id, trashed=trashed)
        migrated = migrate_project_data(data, project_dir)
        return ProjectRecord.from_dict(migrated)

    def _write_project_file(
        self,
        record: ProjectRecord,
        *,
        trashed: bool = False,
        lock_held: bool = False,
    ) -> ProjectRecord:
        project_id = record.project_id
        project_dir = self.paths.project_dir(project_id, trashed=trashed)
        project_dir.mkdir(parents=True, exist_ok=True)
        path = self.paths.project_json(project_id, trashed=trashed)
        lock_path = self.paths.project_lock(project_id)

        record.updated_at = time.time()
        record.storage_version = STORAGE_VERSION
        payload = record.to_dict()

        def _do_write() -> None:
            atomic_write_json(path, payload)

        if lock_held:
            _do_write()
        else:
            with project_write_lock(project_id, lock_path):
                _do_write()

        index = self._trash_index if trashed else self._active_index
        index[project_id] = self._index_row(record, project_dir)
        if trashed:
            self._save_trash_index()
        else:
            self._save_active_index()
        return record

    # ── Public API (§11) ──────────────────────────────────────────────

    def create_project(self, *, title: str = "New Project") -> ProjectRecord:
        """Create a new empty project."""
        record = ProjectRecord.create(title=title)
        project_dir = self.paths.project_dir(record.project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        self._write_project_file(record)
        self.events.publish(
            StorageEvent.PROJECT_CREATED,
            {"project_id": record.project_id, "title": record.title},
        )
        logger.info("Created project %s (%s)", record.project_id, record.title)
        return record

    def open_project(self, project_id: str) -> ProjectRecord:
        """Open a project (acquires cross-process lock, updates last_opened_at)."""
        if is_locked(self.paths.project_lock(project_id)):
            holder_path = self.paths.project_lock(project_id)
            raise StorageLockError(project_id)

        record = self._read_project_file(project_id)
        if record is None:
            record = self._read_project_file(project_id, trashed=True)
            if record is None:
                raise FileNotFoundError(f"Project not found: {project_id}")

        trashed = record.trashed or project_id in self._trash_index
        lock = ProjectFileLock(
            self.paths.project_lock(project_id), project_id=project_id, timeout=5.0
        )
        lock.acquire()

        record.last_opened_at = time.time()
        self._write_project_file(record, trashed=trashed, lock_held=True)

        session_id = str(uuid.uuid4())
        with _MANAGER_LOCK:
            _OPEN_SESSIONS[project_id] = {
                "session_id": session_id,
                "opened_at": time.time(),
                "lock": lock,
            }

        save_recovery_state(
            self.paths,
            project_id,
            title=record.title,
            step="opened",
        )
        self.events.publish(
            StorageEvent.PROJECT_OPENED,
            {"project_id": project_id, "session_id": session_id},
        )
        self.events.publish(
            StorageEvent.SESSION_STARTED,
            {"project_id": project_id, "session_id": session_id},
        )
        return record

    def save_project(self, project_id: str, data: dict[str, Any] | None = None) -> ProjectRecord:
        """Persist project state (atomic, thread-safe)."""
        with project_thread_lock(project_id):
            trashed = project_id in self._trash_index
            record = self._read_project_file(project_id, trashed=trashed)
            if record is None:
                raise FileNotFoundError(f"Project not found: {project_id}")

            if data:
                if "title" in data:
                    record.title = str(data["title"])
                if "studio_session" in data:
                    record.studio_session = dict(data["studio_session"])
                if "tdproj" in data:
                    record.tdproj = dict(data["tdproj"])
                if "metadata" in data:
                    record.metadata.update(dict(data["metadata"]))
                if "legacy_task_id" in data:
                    record.legacy_task_id = str(data["legacy_task_id"])
                if "legacy_project_uuid" in data:
                    record.legacy_project_uuid = str(data["legacy_project_uuid"])

            with _MANAGER_LOCK:
                session_open = project_id in _OPEN_SESSIONS
            if not session_open and is_locked(self.paths.project_lock(project_id)):
                raise StorageLockError(project_id)

            record = self._write_project_file(record, trashed=trashed, lock_held=True)

            with _MANAGER_LOCK:
                sess = _OPEN_SESSIONS.get(project_id)
            if sess:
                save_recovery_state(
                    self.paths,
                    project_id,
                    title=record.title,
                    step="saved",
                    extra={"session_id": sess.get("session_id")},
                )

        self.events.publish(
            StorageEvent.PROJECT_SAVED,
            {"project_id": project_id},
        )
        return record

    def close_project(self, project_id: str) -> None:
        """Release locks and finish session."""
        with _MANAGER_LOCK:
            sess = _OPEN_SESSIONS.pop(project_id, None)
        if sess and sess.get("lock"):
            try:
                sess["lock"].release()
            except Exception:
                pass

        clear_recovery_state(self.paths)
        self.events.publish(
            StorageEvent.PROJECT_CLOSED,
            {"project_id": project_id},
        )
        self.events.publish(
            StorageEvent.SESSION_FINISHED,
            {"project_id": project_id},
        )

    def move_to_trash(self, project_id: str) -> bool:
        """Soft-delete: move project directory to trash."""
        self.close_project(project_id)
        src = self.paths.project_dir(project_id, trashed=False)
        dst = self.paths.project_dir(project_id, trashed=True)
        if not src.is_dir() and project_id not in self._active_index:
            return False

        record = self._read_project_file(project_id) or ProjectRecord(
            project_id=project_id, title=project_id
        )
        record.trashed = True
        record.trashed_at = time.time()

        if src.is_dir():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(str(src), str(dst))
        else:
            dst.mkdir(parents=True, exist_ok=True)

        self._write_project_file(record, trashed=True)
        self._active_index.pop(project_id, None)
        self._save_active_index()

        self.events.publish(
            StorageEvent.PROJECT_TRASHED,
            {"project_id": project_id},
        )
        self.events.publish(
            StorageEvent.PROJECT_REMOVED,
            {"project_id": project_id, "soft": True},
        )
        return True

    def restore_project(self, project_id: str) -> ProjectRecord:
        """Restore a project from trash."""
        record = self._read_project_file(project_id, trashed=True)
        if record is None:
            raise FileNotFoundError(f"Project not in trash: {project_id}")

        src = self.paths.project_dir(project_id, trashed=True)
        dst = self.paths.project_dir(project_id, trashed=False)

        record.trashed = False
        record.trashed_at = 0.0
        record.updated_at = time.time()

        if src.is_dir():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(str(src), str(dst))

        self._write_project_file(record, trashed=False)
        self._trash_index.pop(project_id, None)
        self._save_trash_index()

        self.events.publish(
            StorageEvent.PROJECT_RESTORED,
            {"project_id": project_id},
        )
        return record

    def delete_project(self, project_id: str, *, permanent: bool = False) -> bool:
        """Permanently delete a project (must be in trash unless ``permanent=True``)."""
        self.close_project(project_id)
        in_trash = project_id in self._trash_index
        if not permanent and not in_trash:
            return self.move_to_trash(project_id) is not None

        for trashed in (True, False):
            project_dir = self.paths.project_dir(project_id, trashed=trashed)
            if project_dir.is_dir():
                shutil.rmtree(project_dir, ignore_errors=True)

        self._active_index.pop(project_id, None)
        self._trash_index.pop(project_id, None)
        self._save_active_index()
        self._save_trash_index()

        lock_path = self.paths.project_lock(project_id)
        lock_path.unlink(missing_ok=True)

        self.events.publish(
            StorageEvent.PROJECT_DELETED,
            {"project_id": project_id, "permanent": True},
        )
        return True

    def empty_trash(self) -> int:
        """Permanently delete all trashed projects. Returns count removed."""
        ids = list(self._trash_index.keys())
        for pid in ids:
            self.delete_project(pid, permanent=True)
        self.events.publish(StorageEvent.TRASH_EMPTIED, {"count": len(ids)})
        return len(ids)

    def export_project(self, project_id: str, dest: str | Path) -> Path:
        """Export project directory as a ``.vmproj.zip`` archive."""
        trashed = project_id in self._trash_index
        project_dir = self.paths.project_dir(project_id, trashed=trashed)
        if not project_dir.is_dir():
            raise FileNotFoundError(f"Project not found: {project_id}")

        dest_path = Path(dest)
        if dest_path.suffix != ".zip":
            dest_path = dest_path.with_suffix(".vmproj.zip")

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest_path.with_suffix(".vmproj.zip.writing")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in project_dir.rglob("*"):
                    if file.is_file():
                        zf.write(file, file.relative_to(project_dir))
            tmp.replace(dest_path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

        self.events.publish(
            StorageEvent.PROJECT_EXPORTED,
            {"project_id": project_id, "path": str(dest_path)},
        )
        return dest_path

    def import_project(self, archive: str | Path, *, title: str = "") -> ProjectRecord:
        """Import a ``.vmproj.zip`` archive as a new project."""
        archive_path = Path(archive)
        if not archive_path.is_file():
            raise FileNotFoundError(str(archive_path))

        new_id = str(uuid.uuid4())
        record = ProjectRecord.create(title=title or archive_path.stem, project_id=new_id)
        project_dir = self.paths.project_dir(record.project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(project_dir)

        # Load imported payload but always assign a fresh project_id.
        pj = project_dir / "project.json"
        if pj.is_file():
            data = read_json(pj)
            if isinstance(data, dict):
                data["project_id"] = new_id
                if title:
                    data["title"] = title
                data["source"] = "import"
                data["source_path"] = str(archive_path)
                record = ProjectRecord.from_dict(data).load_migrated(project_dir)
                record.project_id = new_id

        if title:
            record.title = title
        record.source = "import"
        record.source_path = str(archive_path)
        self._write_project_file(record)

        self.events.publish(
            StorageEvent.PROJECT_IMPORTED,
            {"project_id": record.project_id, "source": str(archive_path)},
        )
        return record

    # ── Queries & statistics (§8) ─────────────────────────────────────

    def list_projects(self, *, include_trashed: bool = False) -> list[dict[str, Any]]:
        """List project summaries sorted by ``updated_at`` descending."""
        rows = list(self._active_index.values())
        if include_trashed:
            rows.extend(self._trash_index.values())
        rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
        return rows

    def list_trash(self) -> list[dict[str, Any]]:
        return list(self._trash_index.values())

    def get_project(self, project_id: str) -> ProjectRecord | None:
        rec = self._read_project_file(project_id)
        if rec:
            return rec
        return self._read_project_file(project_id, trashed=True)

    def get_statistics(self) -> dict[str, Any]:
        """Aggregate storage statistics."""
        active = list(self._active_index.values())
        trashed = list(self._trash_index.values())

        def _sum_size(rows: list[dict]) -> int:
            return sum(int(r.get("size_bytes") or 0) for r in rows)

        return {
            "storage_version": STORAGE_VERSION,
            "active_count": len(active),
            "trash_count": len(trashed),
            "total_count": len(active) + len(trashed),
            "active_size_bytes": _sum_size(active),
            "trash_size_bytes": _sum_size(trashed),
            "total_size_bytes": _sum_size(active) + _sum_size(trashed),
            "open_sessions": len(_OPEN_SESSIONS),
            "projects_root": str(self.paths.projects_root),
            "trash_root": str(self.paths.trash_root),
        }

    def is_open(self, project_id: str) -> bool:
        with _MANAGER_LOCK:
            return project_id in _OPEN_SESSIONS

    # ── Events (§7) ───────────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: Callable) -> None:
        self.events.subscribe(event_type, handler)

    # ── Startup (§3, §9, §10) ─────────────────────────────────────────

    def startup(self) -> dict[str, Any]:
        """Run startup tasks: cleanup, legacy migration, recovery check."""
        report: dict[str, Any] = {"storage_version": STORAGE_VERSION}

        cleanup = run_storage_cleanup(self.paths.app_dir)
        report["cleanup"] = cleanup.to_dict()
        self.events.publish(
            StorageEvent.STORAGE_CLEANUP,
            report["cleanup"],
        )

        migrated = self._migrate_legacy_projects()
        report["legacy_migrated"] = migrated

        recovery = check_recovery(self.paths)
        report["recovery"] = recovery

        self._load_indexes()
        report["statistics"] = self.get_statistics()
        logger.info(
            "StorageManager startup: active=%d trash=%d recovery=%s",
            report["statistics"]["active_count"],
            report["statistics"]["trash_count"],
            bool(recovery),
        )
        return report

    def _migrate_legacy_projects(self) -> int:
        """Import legacy studio_sessions and tdproj entries not yet in index."""
        count = 0
        known_ids = set(self._active_index) | set(self._trash_index)

        # Legacy studio sessions: output/studio_sessions/<task_id>.json
        legacy_dir = self.paths.legacy_studio_sessions
        if legacy_dir.is_dir():
            for session_file in legacy_dir.glob("*.json"):
                legacy_id = session_file.stem
                if legacy_id in known_ids:
                    continue
                project_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"legacy-session:{legacy_id}"))
                if project_id in known_ids:
                    continue
                data = migrate_legacy_studio_session(
                    session_file,
                    self.paths.project_dir(project_id),
                    project_id=project_id,
                )
                if data:
                    record = ProjectRecord.from_dict(data)
                    self._write_project_file(record)
                    known_ids.add(project_id)
                    count += 1
                    self.events.publish(
                        StorageEvent.PROJECT_MIGRATED,
                        {"project_id": project_id, "source": "studio_session"},
                    )

        # Legacy tdproj index
        tdproj_index = read_json(self.paths.legacy_tdproj_index) or {}
        for pid, row in dict(tdproj_index.get("projects") or {}).items():
            if pid in known_ids:
                continue
            rel = str(row.get("path") or "")
            tdproj_path = self.paths.app_dir / rel if rel else None
            if not tdproj_path or not tdproj_path.is_file():
                alt = self.paths.legacy_tdproj_root / pid
                files = list(alt.glob("*.tdproj")) if alt.is_dir() else []
                tdproj_path = files[0] if files else None
            if not tdproj_path or not tdproj_path.is_file():
                continue
            storage_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"legacy-tdproj:{pid}"))
            if storage_id in known_ids:
                continue
            data = migrate_legacy_tdproj(
                tdproj_path,
                self.paths.project_dir(storage_id),
                project_id=storage_id,
            )
            if data:
                record = ProjectRecord.from_dict(data)
                record.legacy_task_id = pid
                self._write_project_file(record)
                known_ids.add(storage_id)
                count += 1
                self.events.publish(
                    StorageEvent.PROJECT_MIGRATED,
                    {"project_id": storage_id, "source": "tdproj"},
                )

        return count


# ── Module-level singleton & public functions (§11) ─────────────────────


def get_storage_manager(app_dir: str | Path | None = None) -> StorageManager:
    """Return the process-wide StorageManager singleton."""
    if app_dir is None:
        app_dir = Path(__file__).resolve().parents[2]
    key = str(Path(app_dir).resolve())
    with _MANAGER_LOCK:
        if key not in _MANAGERS:
            _MANAGERS[key] = StorageManager(key)
        return _MANAGERS[key]


def create_project(*, title: str = "New Project", app_dir: str | Path | None = None) -> ProjectRecord:
    return get_storage_manager(app_dir).create_project(title=title)


def open_project(project_id: str, app_dir: str | Path | None = None) -> ProjectRecord:
    return get_storage_manager(app_dir).open_project(project_id)


def save_project(
    project_id: str, data: dict[str, Any] | None = None, app_dir: str | Path | None = None
) -> ProjectRecord:
    return get_storage_manager(app_dir).save_project(project_id, data)


def close_project(project_id: str, app_dir: str | Path | None = None) -> None:
    get_storage_manager(app_dir).close_project(project_id)


def delete_project(
    project_id: str, *, permanent: bool = False, app_dir: str | Path | None = None
) -> bool:
    return get_storage_manager(app_dir).delete_project(project_id, permanent=permanent)


def move_to_trash(project_id: str, app_dir: str | Path | None = None) -> bool:
    return get_storage_manager(app_dir).move_to_trash(project_id)


def restore_project(project_id: str, app_dir: str | Path | None = None) -> ProjectRecord:
    return get_storage_manager(app_dir).restore_project(project_id)


def empty_trash(app_dir: str | Path | None = None) -> int:
    return get_storage_manager(app_dir).empty_trash()


def export_project(
    project_id: str, dest: str | Path, app_dir: str | Path | None = None
) -> Path:
    return get_storage_manager(app_dir).export_project(project_id, dest)


def import_project(
    archive: str | Path, *, title: str = "", app_dir: str | Path | None = None
) -> ProjectRecord:
    return get_storage_manager(app_dir).import_project(archive, title=title)


def list_projects(
    *, include_trashed: bool = False, app_dir: str | Path | None = None
) -> list[dict[str, Any]]:
    return get_storage_manager(app_dir).list_projects(include_trashed=include_trashed)


def get_statistics(app_dir: str | Path | None = None) -> dict[str, Any]:
    return get_storage_manager(app_dir).get_statistics()


def startup_storage(app_dir: str | Path | None = None) -> dict[str, Any]:
    return get_storage_manager(app_dir).startup()


def check_session_recovery(app_dir: str | Path | None = None) -> dict[str, Any] | None:
    mgr = get_storage_manager(app_dir)
    return check_recovery(mgr.paths)
