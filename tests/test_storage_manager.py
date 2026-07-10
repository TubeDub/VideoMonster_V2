"""Storage audit & safe cleanup (TZ Storage §8–§10)."""

from __future__ import annotations

import json
from pathlib import Path


def test_audit_returns_all_buckets(tmp_path):
    from engines.storage_audit import audit_storage

    app = tmp_path
    (app / "output" / "sessions" / "oldtask").mkdir(parents=True)
    (app / "output" / "sessions" / "oldtask" / "seg.mp3").write_bytes(b"x" * 100)
    (app / "output" / "cache" / "pipeline").mkdir(parents=True)
    (app / "data" / "cache").mkdir(parents=True)
    (app / "data" / "cache" / "llm_rewrite_cache.json").write_text("{}", encoding="utf-8")
    (app / "projects" / "tdproj").mkdir(parents=True)
    (app / "projects" / "tdproj" / "p.tdproj").write_text("{}", encoding="utf-8")
    (app / "output" / "video_test_OUTPUT_final.mp4").write_bytes(b"mp4")

    report = audit_storage(app)
    ids = {b["id"] for b in report["buckets"]}
    assert "program" in ids
    assert "models" in ids
    assert "llm" in ids
    assert "cache" in ids
    assert "temp" in ids
    assert "logs" in ids
    assert "projects" in ids
    assert "outputs" in ids
    assert report["total_bytes"] > 0
    proj = next(b for b in report["buckets"] if b["id"] == "projects")
    assert proj["deletable"] is False


def test_cleanup_never_deletes_projects_or_mp4(tmp_path):
    from engines.storage_cleanup import cleanup_all_temp_and_cache

    app = tmp_path
    mp4 = app / "output" / "video_OUTPUT_done.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.write_bytes(b"final video content")
    proj = app / "projects" / "tdproj" / "user.tdproj"
    proj.parent.mkdir(parents=True)
    proj.write_text('{"name":"test"}', encoding="utf-8")

    # Temp session (old enough)
    import os
    import time

    sess = app / "output" / "sessions" / "orphan123"
    sess.mkdir(parents=True)
    (sess / "segment_0.mp3").write_bytes(b"tts")
    old = time.time() - 7200
    os.utime(sess, (old, old))

    slot = app / "output" / "slot_fit" / "t1"
    slot.mkdir(parents=True)
    (slot / "work.tmp").write_bytes(b"tmp")

    report = cleanup_all_temp_and_cache(app)
    assert mp4.is_file(), "final MP4 must never be deleted"
    assert proj.is_file(), "user project must never be deleted"
    assert not sess.exists(), "orphan session should be removed"
    assert report.files_deleted >= 1
    assert report.bytes_freed >= 0


def test_cleanup_report_openddf_shape(tmp_path):
    from engines.storage_cleanup import cleanup_pipeline_temp, StorageCleanupReport

    app = tmp_path
    report = cleanup_pipeline_temp(app, include_sessions=False, include_slot_fit=False)
    d = report.to_dict()
    assert "files_deleted" in d
    assert "directories_cleaned" in d
    assert "directories_skipped" in d
    assert "bytes_freed" in d


def test_openddf_storage_report_block(tmp_path):
    from engines.segment_timing_qa import _build_openddf_storage_report

    info = {
        "session_dir": str(tmp_path / "output" / "sessions" / "t1"),
        "storage_cleanup": {
            "files_deleted": 3,
            "bytes_freed": 1024,
            "mb_freed": 0.0,
            "directories_cleaned": ["/tmp/sess"],
            "directories_skipped": [{"path": "/projects", "reason": "protected"}],
            "scope": "after_dub_complete",
        },
    }
    block = _build_openddf_storage_report(info, app_dir=tmp_path)
    assert block["files_deleted"] == 3
    assert block["directories_cleaned"] == ["/tmp/sess"]
    assert block["cleanup_performed"] is True


def test_storage_api_audit(client=None):
    """Smoke import — Flask client optional in CI."""
    from engines.storage_audit import audit_storage
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1]
    r = audit_storage(app_dir)
    assert isinstance(r.get("buckets"), list)
