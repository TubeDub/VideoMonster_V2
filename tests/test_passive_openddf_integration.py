"""Passive OpenDDF integration — observe only, no pipeline mutation."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError
from engines.pipeline_integrity.guards import StageSnapshotGuard
from engines.pipeline_integrity.openddf_diagnostics import (
    enrich_stage_snapshot_error,
    guard_check_with_diagnostics,
)
from engines.pipeline_integrity.passive_openddf import (
    capture_pipeline_exception,
    ensure_diagnostic_archive,
    ensure_session,
    get_session,
    passive_metadata,
    start_diagnostic_run,
)


def test_start_diagnostic_run_creates_run_id(tmp_path):
    session = start_diagnostic_run("run-1", output_dir=tmp_path)
    assert session is not None
    assert session.run_id == "run-1"
    events = session.timeline.get_events()
    assert events[0]["event_name"] == "diagnostic_context_start"
    assert any(e["event_name"] == "dub_run_start" for e in events)


def test_passive_session_does_not_modify_segments(tmp_path):
    before = [{"segment_id": "s1", "text": "a"}]
    after = [{"segment_id": "s1", "text": "b"}]
    before_id = id(before)
    after_id = id(after)

    ensure_session("passive-task", output_dir=tmp_path)
    with pytest.raises(StageSnapshotIntegrityError):
        guard_check_with_diagnostics(
            before,
            after,
            stage="tts",
            mutator_module="engines.tts",
            task_id="passive-task",
            output_dir=tmp_path,
        )

    assert id(before) == before_id
    assert id(after) == after_id
    assert before[0]["text"] == "a"
    assert after[0]["text"] == "b"


def test_passive_enrich_marks_mode_and_writes_zip(tmp_path):
    before = [{"segment_id": "s1", "slot_fit_key": None}]
    after = [{"segment_id": "s1", "slot_fit_key": "voice_slot_17"}]
    violations = StageSnapshotGuard.diff_violations(before, after, stage="slot_fit")
    first = violations[0]
    exc = StageSnapshotIntegrityError(
        first["message"],
        stage="slot_fit",
        segment_id="s1",
        field="slot_fit_key",
        old_value=None,
        new_value="voice_slot_17",
        allowed_mutations=list(first.get("allowed_mutations") or []),
        mutator_module="engines.slot_fit",
        details={"violations": violations},
    )

    ensure_session("zip-task", output_dir=tmp_path)
    enrich_stage_snapshot_error(
        exc,
        before=before,
        after=after,
        task_id="zip-task",
        output_dir=tmp_path,
    )

    openddf = exc.details["openddf"]
    assert openddf["mode"] == "passive"
    assert openddf["sdk_version"]
    assert openddf["developer"]["mode"] == "passive"
    assert openddf["artifacts"]["diagnostic_zip"]

    session = get_session("zip-task")
    assert session is not None
    assert session.mode == "passive"
    assert any(e["event_name"] == "stage_snapshot_integrity_failed" for e in session.timeline.get_events())


def test_guard_validation_unchanged_under_passive_wrapper(tmp_path):
    """StageSnapshotGuard still rejects the same fields — passive layer only observes."""
    before = [{"segment_id": "s1", "plain_text": "hello"}]
    after = [{"segment_id": "s1", "plain_text": "changed"}]

    with pytest.raises(StageSnapshotIntegrityError) as direct:
        StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")

    with pytest.raises(StageSnapshotIntegrityError) as wrapped:
        guard_check_with_diagnostics(
            before,
            after,
            stage="tts",
            mutator_module="engines.tts",
            task_id="compare-task",
            output_dir=tmp_path,
        )

    assert direct.value.field == wrapped.value.field


def test_generic_exception_creates_full_diagnostic_zip(tmp_path):
    start_diagnostic_run("crash-1", output_dir=tmp_path)
    exc = RuntimeError("pipeline exploded")
    arts = capture_pipeline_exception("crash-1", exc, stage="TTS", output_dir=tmp_path)
    assert arts.get("diagnostic_zip")
    zip_path = Path(arts["diagnostic_zip"])
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert names >= {
            "pipeline.log",
            "stacktrace.txt",
            "environment.json",
            "report.json",
            "snapshot_before.json",
            "snapshot_after.json",
            "snapshot_diff.json",
        }
        env = json.loads(zf.read("environment.json"))
        assert "python_version" in env or "os" in env


def test_ensure_diagnostic_archive_builds_when_missing(tmp_path):
    start_diagnostic_run("ensure-1", output_dir=tmp_path)
    path = ensure_diagnostic_archive("ensure-1", output_dir=tmp_path)
    assert path
    assert Path(path).is_file()


def test_publish_task_diagnostic_from_artifacts(tmp_path):
    start_diagnostic_run("pub-1", output_dir=tmp_path)
    arts = capture_pipeline_exception(
        "pub-1",
        RuntimeError("boom"),
        stage="TTS",
        output_dir=tmp_path,
    )
    from engines.pipeline_integrity.passive_openddf import publish_task_diagnostic

    meta = publish_task_diagnostic("pub-1", artifacts=arts, output_dir=tmp_path)
    assert meta.get("diagnostic_zip_available") is True
    assert Path(meta["diagnostic_zip"]).is_file()


def test_diagnostic_status_for_task_finds_zip_on_disk(tmp_path):
    start_diagnostic_run("disk-1", output_dir=tmp_path)
    arts = capture_pipeline_exception(
        "disk-1",
        RuntimeError("boom"),
        stage="TTS",
        output_dir=tmp_path,
    )
    from engines.pipeline_integrity.passive_openddf import diagnostic_status_for_task

    status = diagnostic_status_for_task(
        "disk-1",
        {"openddf_artifacts": {"diagnostic_zip": arts["diagnostic_zip"]}},
    )
    assert status["diagnostic_zip_available"] is True
    assert status["diagnostic_zip"] == arts["diagnostic_zip"]


def test_publish_task_diagnostic_sets_created_status(tmp_path):
    start_diagnostic_run("status-1", output_dir=tmp_path)
    arts = capture_pipeline_exception(
        "status-1",
        RuntimeError("boom"),
        stage="TTS",
        output_dir=tmp_path,
    )
    from engines.pipeline_integrity.passive_openddf import publish_task_diagnostic

    meta = publish_task_diagnostic("status-1", artifacts=arts, output_dir=tmp_path)
    assert meta["diagnostic_zip_status"] == "created"
    assert meta["diagnostic_zip_available"] is True
    assert meta["diagnostic_zip_reason"] is None


def test_auto_dub_slot_fit_key_helpers():
    from api.auto_dub_api import (
        _segment_slot_fit_key,
        _set_segment_slot_fit_key,
        _slot_fit_content_key,
    )

    seg = {"timing_meta": {"strategy": "trim"}}
    key = _slot_fit_content_key("hello", "ru-RU-DmitryNeural", 1200, None, None)
    _set_segment_slot_fit_key(seg, key)
    assert _segment_slot_fit_key(seg) == key
    assert seg["timing_meta"]["slot_fit_key"] == key
    assert "slot_fit_key" not in seg


def test_diagnostic_save_endpoint_copies_zip(tmp_path, monkeypatch):
    from api.auto_dub_api import AUTO_TASKS, STATE_LOCK, api_auto_dub_diagnostic_save

    zip_path = tmp_path / "diagnostic_task-save.zip"
    zip_path.write_bytes(b"PK\x03\x04fake")
    with STATE_LOCK:
        AUTO_TASKS["task-save"] = {"status": "error", "info": {}}

    monkeypatch.setattr(
        "engines.pipeline_integrity.passive_openddf.ensure_diagnostic_archive",
        lambda task_id, **kwargs: str(zip_path),
    )
    dest = tmp_path / "saved" / "my_diag.zip"
    dest.parent.mkdir()

    def fake_asksaveas(**kwargs):
        return str(dest)

    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", fake_asksaveas)
    monkeypatch.setattr("tkinter.Tk", lambda: type("R", (), {
        "withdraw": lambda self: None,
        "attributes": lambda self, *a, **k: None,
        "destroy": lambda self: None,
    })())

    from app import app as flask_app

    with flask_app.test_request_context(
        "/api/auto_dub/diagnostics/task-save/save",
        method="POST",
        json={},
    ):
        resp = api_auto_dub_diagnostic_save("task-save")
        if isinstance(resp, tuple):
            body, status = resp
        else:
            body, status = resp, 200
        data = body.get_json()
    assert status == 200
    assert data["success"] is True
    assert dest.is_file()
    assert dest.read_bytes() == zip_path.read_bytes()


def test_artificial_stage_snapshot_integrity_zip(tmp_path):
    """TZ §7 — StageSnapshotIntegrityError produces full diagnostic archive."""
    start_diagnostic_run("ssie-zip", output_dir=tmp_path)
    before = [{"segment_id": "s1", "slot_fit_key": None}]
    after = [{"segment_id": "s1", "slot_fit_key": "voice_slot_17"}]
    with pytest.raises(StageSnapshotIntegrityError):
        guard_check_with_diagnostics(
            before,
            after,
            stage="slot_fit",
            mutator_module="engines.slot_fit",
            task_id="ssie-zip",
            output_dir=tmp_path,
        )
    meta = passive_metadata("ssie-zip")
    assert meta.get("diagnostic_zip")
    with zipfile.ZipFile(meta["diagnostic_zip"]) as zf:
        assert "report.json" in zf.namelist()
        assert "environment.json" in zf.namelist()
