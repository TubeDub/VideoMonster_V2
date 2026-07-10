"""Tests for Storage Manager Phase 1 (TZ §14)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from engines.storage.atomic import atomic_write_json, read_json
from engines.storage.events import StorageEvent
from engines.storage.locks import ProjectFileLock, StorageLockError, is_locked
from engines.storage.manager import (
    StorageManager,
    check_session_recovery,
    close_project,
    create_project,
    delete_project,
    empty_trash,
    export_project,
    get_storage_manager,
    import_project,
    move_to_trash,
    open_project,
    restore_project,
    save_project,
    startup_storage,
)
from engines.storage.migration import STORAGE_VERSION, migrate_project_data
from engines.storage.recovery import save_recovery_state


@pytest.fixture
def app_dir(tmp_path):
    """Isolated app directory for storage tests."""
    root = tmp_path / "app"
    root.mkdir()
    (root / "output").mkdir()
    (root / "data").mkdir()
    return root


@pytest.fixture
def mgr(app_dir):
    return StorageManager(app_dir)


# ── Atomic writes ─────────────────────────────────────────────────────


def test_atomic_write_survives_read(app_dir):
    path = app_dir / "data" / "test.json"
    atomic_write_json(path, {"key": "value"})
    assert read_json(path) == {"key": "value"}


def test_atomic_write_no_partial_on_error(app_dir):
    path = app_dir / "data" / "safe.json"
    atomic_write_json(path, {"v": 1})
    with pytest.raises(TypeError):
        atomic_write_json(path, object())  # not JSON serializable
    assert read_json(path) == {"v": 1}


# ── Migration ─────────────────────────────────────────────────────────


def test_migrate_v0_to_v1():
    data = {"project_id": "abc", "title": "Test"}
    migrated = migrate_project_data(data, Path("/tmp/unused"))
    assert migrated["storage_version"] == STORAGE_VERSION
    assert "created_at" in migrated
    assert migrated.get("trashed") is False


# ── CRUD ──────────────────────────────────────────────────────────────


def test_create_open_save_close(mgr):
    record = mgr.create_project(title="My Dub")
    assert record.project_id
    assert record.storage_version == STORAGE_VERSION

    opened = mgr.open_project(record.project_id)
    assert opened.last_opened_at > 0

    opened.studio_session = {"segments": [{"text": "hello"}]}
    saved = mgr.save_project(record.project_id, {"studio_session": opened.studio_session})
    assert saved.studio_session["segments"][0]["text"] == "hello"

    mgr.close_project(record.project_id)
    assert not mgr.is_open(record.project_id)


def test_project_json_has_storage_version(mgr, app_dir):
    record = mgr.create_project(title="Version Check")
    pj = app_dir / "projects" / "vm_storage" / record.project_id / "project.json"
    data = json.loads(pj.read_text(encoding="utf-8"))
    assert data["storage_version"] == STORAGE_VERSION


# ── Trash ───────────────────────────────────────────────────────────


def test_trash_restore_delete(mgr):
    record = mgr.create_project(title="Trash Me")
    pid = record.project_id

    assert mgr.move_to_trash(pid)
    assert pid not in mgr._active_index
    assert pid in mgr._trash_index

    restored = mgr.restore_project(pid)
    assert not restored.trashed
    assert pid in mgr._active_index

    mgr.move_to_trash(pid)
    mgr.delete_project(pid, permanent=True)
    assert pid not in mgr._active_index
    assert pid not in mgr._trash_index


def test_empty_trash(mgr):
    for i in range(3):
        r = mgr.create_project(title=f"T{i}")
        mgr.move_to_trash(r.project_id)
    count = mgr.empty_trash()
    assert count == 3
    assert len(mgr.list_trash()) == 0


# ── Export / Import ──────────────────────────────────────────────────


def test_export_import_roundtrip(mgr, app_dir):
    record = mgr.create_project(title="Export Test")
    mgr.save_project(record.project_id, {"studio_session": {"foo": "bar"}})

    archive = app_dir / "output" / "test.vmproj.zip"
    exported = mgr.export_project(record.project_id, archive)
    assert exported.is_file()

    imported = mgr.import_project(archive, title="Imported")
    assert imported.title == "Imported"
    assert imported.project_id != record.project_id


# ── Statistics ───────────────────────────────────────────────────────


def test_statistics(mgr):
    mgr.create_project(title="S1")
    mgr.create_project(title="S2")
    stats = mgr.get_statistics()
    assert stats["active_count"] == 2
    assert stats["storage_version"] == STORAGE_VERSION
    assert "total_size_bytes" in stats


# ── Events ───────────────────────────────────────────────────────────


def test_events_fired_on_create(mgr):
    received = []
    mgr.subscribe(StorageEvent.PROJECT_CREATED, lambda evt, p: received.append(evt))
    mgr.create_project(title="Event Test")
    assert StorageEvent.PROJECT_CREATED in received


# ── Session recovery ──────────────────────────────────────────────────


def test_recovery_after_crash(mgr, app_dir):
    record = mgr.create_project(title="Recovery")
    mgr.open_project(record.project_id)
    save_recovery_state(mgr.paths, record.project_id, title="Recovery", step="translate")

    # Simulate crash — no close_project()
    recovery = check_session_recovery(app_dir)
    assert recovery is not None
    assert recovery["project_id"] == record.project_id


def test_recovery_cleared_on_close(mgr, app_dir):
    record = mgr.create_project(title="Recovery Close")
    mgr.open_project(record.project_id)
    mgr.close_project(record.project_id)
    assert check_session_recovery(app_dir) is None


# ── Locks ────────────────────────────────────────────────────────────


def test_thread_lock_prevents_concurrent_write(mgr):
    record = mgr.create_project(title="Lock Test")
    errors = []

    def writer():
        try:
            for _ in range(5):
                mgr.save_project(record.project_id, {"metadata": {"n": time.time()}})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_file_lock_blocks_second_acquire(app_dir):
    lock_path = app_dir / "data" / "locks" / "test.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock1 = ProjectFileLock(lock_path, project_id="test")
    lock1.acquire()
    assert is_locked(lock_path) is False  # our own pid
    lock1.release()


# ── Legacy migration ───────────────────────────────────────────────────


def test_legacy_studio_session_import(mgr, app_dir):
    legacy_dir = app_dir / "output" / "studio_sessions"
    legacy_dir.mkdir(parents=True)
    session_file = legacy_dir / "task-abc123.json"
    session_file.write_text(
        json.dumps({"session_id": "task-abc123", "title": "Legacy Dub"}),
        encoding="utf-8",
    )
    count = mgr._migrate_legacy_projects()
    assert count >= 1
    projects = mgr.list_projects()
    assert any("Legacy" in p.get("title", "") for p in projects)


# ── Startup ──────────────────────────────────────────────────────────


def test_startup_runs_cleanup_and_migration(mgr, app_dir):
    report = mgr.startup()
    assert "cleanup" in report
    assert "statistics" in report
    assert report["storage_version"] == STORAGE_VERSION


# ── Public API wrappers ───────────────────────────────────────────────


def test_public_api_functions(app_dir):
    # Reset singleton for isolated app_dir
    from engines.storage import manager as mgr_mod

    mgr_mod._MANAGERS.clear()

    record = create_project(title="API Test", app_dir=app_dir)
    open_project(record.project_id, app_dir=app_dir)
    save_project(record.project_id, {"title": "Renamed"}, app_dir=app_dir)
    close_project(record.project_id, app_dir=app_dir)

    move_to_trash(record.project_id, app_dir=app_dir)
    restore_project(record.project_id, app_dir=app_dir)

    report = startup_storage(app_dir)
    assert report["storage_version"] == STORAGE_VERSION
