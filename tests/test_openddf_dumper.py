"""OpenDDF DiagnosticDumper unit tests (spec §8.1)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from openddf import (
    DiagnosticContext,
    DiagnosticDumper,
    SnapshotGuard,
    StageSnapshotIntegrityError,
    TimelineTracker,
    filter_sensitive_data,
)
from openddf.utils import REDACTED


def test_filter_sensitive_data_masks_secrets():
    payload = {
        "api_key": "super-secret",
        "username": "alice",
        "nested": {"auth_token": "tok"},
    }
    filtered = filter_sensitive_data(payload)
    assert filtered["api_key"] == REDACTED
    assert filtered["nested"]["auth_token"] == REDACTED
    assert filtered["username"] == "alice"


def test_filter_sensitive_data_preserves_slot_fit_key():
    payload = {
        "timing_meta": {"slot_fit_key": "4d5d127f970a2cfd"},
        "secret_key": "must-hide",
    }
    filtered = filter_sensitive_data(payload)
    assert filtered["timing_meta"]["slot_fit_key"] == "4d5d127f970a2cfd"
    assert filtered["secret_key"] == REDACTED


def test_dumper_creates_zip_with_flat_structure(tmp_path):
    timeline = TimelineTracker()
    timeline.add_event("step_one", "OK")
    timeline.add_event("step_two", "FAILED")

    dumper = DiagnosticDumper(tmp_path, "run-42")
    exc = StageSnapshotIntegrityError(
        "bad field",
        field_name="status",
        old_value="a",
        new_value="b",
        allowed_mutations=["count"],
        location_info={"file": "app.py", "line": 10, "function": "run"},
    )
    before = {"status": "a", "api_key": "secret"}
    after = {"status": "b", "api_key": "secret"}

    zip_path = dumper.dump_crash(exc, timeline, snapshot_before=before, snapshot_after=after)
    assert Path(zip_path).is_file()
    assert zip_path.endswith("diagnostic_run-42.zip")

    temp_dirs = list((tmp_path / "diagnostics").glob("diagnostic_run-42_*"))
    assert temp_dirs == []

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert names == {
            "pipeline.log",
            "stacktrace.txt",
            "environment.json",
            "report.json",
            "snapshot_before.json",
            "snapshot_after.json",
            "snapshot_diff.json",
        }
        assert "/" not in "".join(names)

        report = json.loads(zf.read("report.json"))
        assert report["framework"] == "OpenDDF"
        assert report["version"] == "0.1.0"
        assert report["run_id"] == "run-42"
        assert report["recovery_hint"]["root_cause"]

        snap_before = json.loads(zf.read("snapshot_before.json"))
        assert snap_before["api_key"] == REDACTED


def test_diagnostic_context_produces_archive_on_crash(tmp_path):
    data = {"status": "ok"}
    ctx = DiagnosticContext("ctx-1", tmp_path)
    with pytest.raises(StageSnapshotIntegrityError):
        with ctx:
            ctx.register_snapshots({"status": "ok"}, {"status": "bad"})
            with SnapshotGuard(data, allowed_mutations=set()):
                data["status"] = "bad"
    assert ctx.last_archive_path
    archive = tmp_path / "diagnostics" / "diagnostic_ctx-1.zip"
    assert archive.is_file()
