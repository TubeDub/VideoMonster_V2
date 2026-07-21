"""TZ v2.0 — Runtime Integrity, Recovery, Plugins, Crash, Golden, Observability."""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.pipeline_integrity.contract_versions import (
    CONTRACT_VERSION_KEYS,
    stamp_contract_versions,
    require_contract_versions,
)
from engines.pipeline_integrity.crash_recovery import (
    load_checkpoint,
    resume_stage_from_checkpoint,
    save_checkpoint,
)
from engines.pipeline_integrity.error_recovery import (
    RecoveryAction,
    plan_recovery,
    recover_missing_tts,
)
from engines.pipeline_integrity.golden_dataset import (
    assert_matches_golden,
    ensure_golden_layout,
)
from engines.pipeline_integrity.observability import health_dashboard, record_segment_event
from engines.pipeline_integrity.pipeline_state import PipelineState, advance_pipeline_state
from engines.pipeline_integrity.plugin_registry import (
    bootstrap_builtin_plugins,
    list_plugins,
)
from engines.pipeline_integrity.exceptions import RuntimeIntegrityError
from engines.pipeline_integrity.runtime_validator import (
    enforce_runtime,
    validate_runtime,
    write_diagnostic_zip,
)
from engines.perf_budgets import (
    DIAGNOSTICS_BUDGET_MS,
    RUNTIME_INTEGRITY_BUDGET_MS,
    measure_budget,
)


def test_all_five_contract_versions():
    info: dict = {}
    stamped = stamp_contract_versions(info)
    assert set(stamped) == set(CONTRACT_VERSION_KEYS)
    require_contract_versions(info)


def test_handoff_state_in_fsm():
    info = {"pipeline_state": "MERGED"}
    advance_pipeline_state(info, PipelineState.HANDOFF)
    advance_pipeline_state(info, PipelineState.EXPORTED)
    assert info["pipeline_state"] == "EXPORTED"


def test_runtime_validator_passes_locked_project(tmp_path):
    info = {
        "task_id": "rt1",
        "translation_locked": True,
        "segments_data": [
            {
                "segment_id": "s1",
                "segment_uuid": "s1",
                "start_ms": 0,
                "end_ms": 1000,
                "slot_ms": 1000,
                "file": "a.wav",
            }
        ],
    }
    stamp_contract_versions(info)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")

    def _resolve(name, task_info=None):
        return tmp_path / Path(name).name

    result = validate_runtime(
        info, stage="studio_handoff", require_tts=True, resolve_audio=_resolve
    )
    assert result.ok


def test_runtime_validator_fails_missing_tts_and_writes_zip(tmp_path):
    info = {
        "task_id": "rt2",
        "translation_locked": True,
        "segments_data": [
            {
                "segment_id": "s1",
                "segment_uuid": "s1",
                "start_ms": 0,
                "end_ms": 1000,
                "slot_ms": 1000,
                # no file
            }
        ],
    }
    stamp_contract_versions(info)
    with pytest.raises(RuntimeIntegrityError) as exc:
        enforce_runtime(
            info,
            stage="studio_handoff",
            require_tts=True,
            output_dir=tmp_path,
            resolve_audio=lambda n, task_info=None: tmp_path / "missing.wav",
        )
    assert "diagnostic_zip" in (exc.value.details or {})
    zips = list(tmp_path.glob("runtime_integrity_*.zip"))
    assert zips


def test_recovery_tts_missing_then_skip():
    plan = plan_recovery("tts_file_not_found", segment_id="s1", attempt=0)
    assert plan.action == RecoveryAction.REGENERATE_TTS
    plan2 = plan_recovery("tts_file_not_found", segment_id="s1", attempt=2)
    assert plan2.action == RecoveryAction.SKIP_SEGMENT
    seg = {"segment_id": "s1"}
    out = recover_missing_tts(seg, regen_fn=None, attempt=2)
    assert out.action == RecoveryAction.SKIP_SEGMENT
    assert seg["status"] == "skipped_recovery"


def test_crash_checkpoint_roundtrip(tmp_path):
    info = {
        "task_id": "c1",
        "pipeline_state": "LOCKED",
        "translation_locked": True,
        "segments_data": [{"segment_id": "s1", "file": "x.wav", "slot_ms": 500}],
    }
    stamp_contract_versions(info)
    path = save_checkpoint(tmp_path, info, stage="lock")
    assert path.is_file()
    loaded = load_checkpoint(tmp_path)
    assert loaded["pipeline_state"] == "LOCKED"
    nxt = resume_stage_from_checkpoint(info, tmp_path)
    assert nxt == "tts"


def test_plugins_bootstrap():
    bootstrap_builtin_plugins()
    plugins = list_plugins("tts")
    ids = {p.id for p in plugins}
    assert "mock" in ids
    assert "edge-offline" in ids or "coqui" in ids


def test_golden_fingerprint_stable(tmp_path):
    ensure_golden_layout(tmp_path)
    segs = [
        {"segment_id": "a", "start_ms": 0, "end_ms": 100, "translated_text": "hi"},
        {"segment_id": "b", "start_ms": 100, "end_ms": 200, "translated_text": "bye"},
    ]
    fp1 = assert_matches_golden("sample", segs, settings={"k": 1}, root=tmp_path)
    fp2 = assert_matches_golden("sample", segs, settings={"k": 1}, root=tmp_path)
    assert fp1 == fp2


def test_observability_health():
    info = {
        "pipeline_state": "HANDOFF",
        "translation_locked": True,
        "segments_data": [{"segment_id": "s1", "file": "a.wav"}],
    }
    seg = info["segments_data"][0]
    record_segment_event(seg, stage="tts", event="synthesized")
    dash = health_dashboard(info)
    assert dash["segments"] == 1
    assert "execution_graph" in dash
    assert len(seg["segment_history"]) == 1


def test_runtime_and_diagnostics_budgets():
    assert RUNTIME_INTEGRITY_BUDGET_MS == 10.0
    assert DIAGNOSTICS_BUDGET_MS == 5.0
    with measure_budget("runtime_integrity", enforce=False) as sample:
        validate_runtime({"segments_data": [{"segment_id": "s"}]}, stage="bootstrap", require_contracts=False)
    assert sample.elapsed_ms < 1000
