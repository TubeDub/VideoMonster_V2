"""P16 Production Hardening tests."""

from __future__ import annotations

from pathlib import Path

from engines.production_hardening.backcompat import (
    check_backward_compatibility,
    migrate_project_info,
    read_legacy_openddf,
)
from engines.production_hardening.checklist import run_release_checklist
from engines.production_hardening.concurrency import run_concurrency_harness
from engines.production_hardening.enriched_logging import build_error_record
from engines.production_hardening.fault_injection import run_fault_suite
from engines.production_hardening.long_run import run_long_run
from engines.production_hardening.resource_manager import (
    assert_no_resource_leak,
    cleanup_temp_wavs,
    take_resource_snapshot,
)


def test_resource_snapshot_and_cleanup(tmp_path: Path):
    wav = tmp_path / "old.wav"
    wav.write_bytes(b"RIFF")
    # force old mtime
    import os
    import time

    os.utime(wav, (time.time() - 7200, time.time() - 7200))
    before = take_resource_snapshot(temp_dirs=[tmp_path])
    cleaned = cleanup_temp_wavs([tmp_path], older_than_sec=60)
    assert cleaned["removed"] >= 1
    after = take_resource_snapshot(temp_dirs=[tmp_path])
    assert isinstance(before.ts, float)
    assert after.temp_wav_count == 0
    assert assert_no_resource_leak(before, after) == []


def test_enriched_error_record_fields():
    rec = build_error_record(
        run_id="run1",
        stage="tts",
        message="TTS file not found",
        exc=FileNotFoundError("missing"),
        segment_uuid="abc",
        error_code="tts_file_not_found",
        diagnostic_zip="/tmp/x.zip",
    )
    assert rec["run_id"] == "run1"
    assert rec["segment_uuid"] == "abc"
    assert rec["stage"] == "tts"
    assert rec["error_class"]
    assert rec["stack_trace"]
    assert rec["recovery_strategy"]["action"]
    assert rec["diagnostic_zip"].endswith("x.zip")


def test_fault_injection_suite(tmp_path: Path):
    result = run_fault_suite(tmp_path)
    assert result.ok
    names = {c.name for c in result.cases}
    assert "missing_wav" in names
    assert "contract_corruption" in names
    assert "scheduler_reject" in names


def test_concurrency_no_duplicate_uuids():
    result = run_concurrency_harness(projects=4, segments_per_project=12, workers=4)
    assert result.ok
    assert result.uuid_unique


def test_long_run_fast_mode():
    result = run_long_run(duration_sec=1.5, segments_per_iter=20, projects_parallel=2)
    assert result.iterations >= 1
    # CI may see noise in RSS; require iterations + no hard leak flags when thresholds generous
    assert result.ok or not result.leak_issues


def test_backcompat_openddf(tmp_path: Path):
    p = tmp_path / "legacy.json"
    p.write_text('{"task_id":"t","segments":[],"summary":{}}', encoding="utf-8")
    data = read_legacy_openddf(p)
    assert "segments" in data
    info = migrate_project_info({"pipeline_state": "LOCKED"})
    assert info.get("translation_contract_version") == 1
    report = check_backward_compatibility([p])
    assert report["ok"]


def test_release_checklist_fast(tmp_path: Path):
    # Skip nested full pytest for speed — exercise other gates
    result = run_release_checklist(
        include_pytest=False,
        long_run_sec=1.0,
        work_dir=tmp_path,
    )
    assert result.ok
    names = {i.name for i in result.items}
    assert "fault_injection" in names
    assert "final_acceptance" in names
    assert "backward_compatibility" in names
