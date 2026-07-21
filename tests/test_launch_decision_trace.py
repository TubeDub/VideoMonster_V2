"""Regression tests for Launch Decision Trace instrumentation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.launch_decision_trace import (
    AI_AGENT_SLOTS,
    LAUNCH_STAGES,
    fail_stt_zero_segments,
    record_agent,
    record_stage,
    seed_ai_agent_slots,
    summarize,
)


def _read_ndjson(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def test_fail_stt_zero_segments_hard_fails_with_named_reason(tmp_path: Path):
    log_path = tmp_path / "trace.ndjson"
    task_info: dict = {}

    with pytest.raises(ArchitectureViolation) as exc_info:
        fail_stt_zero_segments(
            task_info=task_info,
            raw_count=0,
            log_path=log_path,
            line=7444,
        )

    assert exc_info.value.stage == "STT"
    assert exc_info.value.rule == "stt_segment_count_min_1"

    stages = {row["stage"]: row for row in task_info["launch_decision_trace"]["stages"]}
    assert stages["STT Started"]["status"] == "FAILED"
    assert stages["STT Started"]["reason"] == "stt_completed_zero_segments"
    assert stages["Words Built"]["status"] == "FAILED"
    assert stages["Words Built"]["reason"] == "stt_zero_segments_no_handoff"
    assert stages["Meaning Pipeline"]["status"] == "SKIPPED"
    assert "not_called" not in json.dumps(stages)


def test_forbidden_not_called_reason_rejected():
    with pytest.raises(ValueError, match="not_called"):
        record_stage(
            "STT Started",
            status="FAILED",
            reason="not_called",
        )


def test_meaning_first_path_records_stage_success(tmp_path: Path, monkeypatch):
    """Phase2 full run emits SUCCESS for every in-pipeline stage."""
    log_path = tmp_path / "phase2_trace.ndjson"
    monkeypatch.setattr(
        "engines.semantic_v3.launch_decision_trace._DEFAULT_DEBUG_LOG_PATH",
        log_path,
    )

    from engines.semantic_v3.phase2 import run_semantic_v3_phase2

    run_semantic_v3_phase2(
        ["Hi.", "This is a longer second sentence for timing."],
        [{"start": 0, "end": 500}, {"start": 500, "end": 7000}],
        translate=True,
        translate_fn=lambda t, s, tg: f"[{tg}]{t}",
        tgt_lang="uk",
    )

    rows = _read_ndjson(log_path)
    stage_rows = [r for r in rows if r.get("kind") == "stage"]
    by_stage = {r["stage"]: r for r in stage_rows}

    expected_success = {
        "Meaning Pipeline",
        "Sentence Builder",
        "Variant Generator",
        "Duration Predictor",
        "Meaning Fit",
        "Translation",
        "Adaptation",
        "Timeline",
        "Scheduler",
    }
    for name in expected_success:
        assert name in by_stage, f"missing stage record: {name}"
        assert by_stage[name]["status"] == "SUCCESS", (
            f"{name} expected SUCCESS got {by_stage[name]['status']}"
        )
        assert by_stage[name]["reason"]
        assert by_stage[name]["reason"] != "not_called"

    assert by_stage["TTS"]["status"] == "SKIPPED"
    assert "require_wav_files" in by_stage["TTS"]["reason"]


def test_every_agent_slot_has_decision_taker(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "agents.ndjson"
    task_info: dict = {}
    seed_ai_agent_slots(task_info)

    for slot in AI_AGENT_SLOTS:
        row = task_info["launch_decision_trace"]["agents"][slot]
        assert row["status"] == "SKIPPED"
        assert row.get("skipped_reason")
        assert row["skipped_reason"] != "not_called"

    record_agent(
        "semantic",
        called=True,
        called_by="engines/semantic_v3/phase2.py:155",
        task_info=task_info,
        log_path=log_path,
    )
    record_agent(
        "translation",
        called=False,
        skipped_reason="translate_disabled_test_fixture",
        task_info=task_info,
        log_path=log_path,
    )

    summary = summarize(task_info)
    agents = summary["agents"]
    assert agents["semantic"]["status"] == "CALLED"
    assert agents["semantic"]["called_by"]
    assert agents["translation"]["skipped_reason"] == "translate_disabled_test_fixture"

    for slot in AI_AGENT_SLOTS:
        row = agents[slot]
        if row.get("status") == "CALLED":
            assert row.get("called_by")
        else:
            assert row.get("skipped_reason")
            assert row["skipped_reason"] != "not_called"


def test_launch_stages_constant_matches_tz():
    assert "Words Built" in LAUNCH_STAGES
    assert "Meaning Pipeline" in LAUNCH_STAGES
    assert LAUNCH_STAGES.index("STT Started") < LAUNCH_STAGES.index("Words Built")
    assert LAUNCH_STAGES.index("Words Built") < LAUNCH_STAGES.index("Meaning Pipeline")
