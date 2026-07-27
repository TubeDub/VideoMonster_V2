"""Storage Manager CRUD API + TTL upload cleanup smoke tests."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from flask import Flask


@pytest.fixture()
def storage_client(tmp_path, monkeypatch):
    import api.storage_api as storage_api

    monkeypatch.setattr(storage_api, "APP_DIR", tmp_path)

    app = Flask("storage_crud")
    app.register_blueprint(storage_api.bp)
    return app.test_client(), tmp_path


def _destructive_headers():
    return {
        "Content-Type": "application/json",
        "X-VM-Destructive-Confirm": "1",
    }


def test_storage_projects_crud_trash_restore(storage_client):
    client, _app_dir = storage_client

    created = client.post("/api/storage/projects", json={"title": "CRUD Test"})
    assert created.status_code == 200
    body = created.get_json()
    assert body["ok"] is True
    pid = body["project"]["project_id"]

    listed = client.get("/api/storage/projects").get_json()
    assert listed["ok"] is True
    assert any(p["project_id"] == pid for p in listed["projects"])

    trashed = client.post(
        f"/api/storage/projects/{pid}/trash",
        headers=_destructive_headers(),
        json={"confirm": True},
    )
    assert trashed.status_code == 200
    assert trashed.get_json()["ok"] is True

    trash = client.get("/api/storage/trash").get_json()
    assert any(p["project_id"] == pid for p in trash["projects"])

    restored = client.post(f"/api/storage/projects/{pid}/restore", json={})
    assert restored.status_code == 200
    assert restored.get_json()["ok"] is True

    client.post(
        f"/api/storage/projects/{pid}/trash",
        headers=_destructive_headers(),
        json={"confirm": True},
    )
    deleted = client.delete(
        f"/api/storage/projects/{pid}?permanent=1",
        headers=_destructive_headers(),
        json={"confirm": True},
    )
    assert deleted.status_code == 200
    assert deleted.get_json()["ok"] is True


def test_cleanup_stale_imports_ttl(tmp_path):
    from engines.storage_cleanup import cleanup_stale_imports

    imports = tmp_path / "uploads" / "imports"
    imports.mkdir(parents=True)
    old = imports / "oldabc123def0.mp4"
    meta = imports / "oldabc123def0.meta.json"
    fresh = imports / "newabc123def1.wav"
    old.write_bytes(b"old")
    meta.write_text("{}", encoding="utf-8")
    fresh.write_bytes(b"new")
    old_mtime = time.time() - (8 * 24 * 3600)
    os.utime(old, (old_mtime, old_mtime))
    os.utime(meta, (old_mtime, old_mtime))

    report = cleanup_stale_imports(tmp_path, max_age_sec=7 * 24 * 3600)
    assert not old.exists()
    assert not meta.exists()
    assert fresh.exists()
    assert report.files_deleted >= 2


def test_cleanup_stale_translate_uploads_ttl(tmp_path):
    from engines.storage_cleanup import cleanup_stale_translate_uploads

    folder = tmp_path / "uploads" / "translate"
    folder.mkdir(parents=True)
    stale = folder / "audio_deadbeef01.wav"
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 90000, time.time() - 90000))
    report = cleanup_stale_translate_uploads(tmp_path, max_age_sec=86400)
    assert not stale.exists()
    assert report.files_deleted >= 1
