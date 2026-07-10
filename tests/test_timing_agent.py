"""Tests for TubeDub Timing Agent v1.0."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.timing_agent.adaptive_rewriter import generate_adaptive_candidates
from engines.ai_core.timing_agent.agent import TimingAgent
from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
from engines.ai_core.timing_agent.micro_stretch import (
    MICRO_STRETCH_MAX,
    should_apply_micro_stretch,
)
from engines.ai_core.timing_agent.retry_policy import apply_retry_policy
from engines.ai_core.timing_agent.rule_rewrite import generate_shorten_candidates
from engines.ai_core.timing_agent.scoring import timing_score
from engines.ai_core.timing_agent.validators.slot_fit_validator import slot_fit_score
from engines.ai_core.timing_agent.validators.sentence_integrity import (
    validate_sentence_integrity,
)


def _manifest(tmp_path: Path) -> dict:
    project_uuid = str(uuid.uuid4())
    manifest_dir = tmp_path / "manifests" / project_uuid
    manifest_dir.mkdir(parents=True)
    return {
        "project_uuid": project_uuid,
        "planner_version": "3.0",
        "source_lang": "en",
        "target_lang": "ru",
        "capability_matrix": {"llm": False},
    }


def _segments_short_slot() -> list[dict]:
    long_text = (
        "Он сказал что George Smith пошёл домой в настоящее время "
        "потому что было очень поздно и он устал."
    )
    return [
        {
            "index": 0,
            "text": "He said George Smith went home because it was very late.",
            "translated_text": long_text,
            "semantic_text": long_text,
            "start": 0,
            "end": 1200,
            "speaker": "A",
        }
    ]


def _segments_long_slot() -> list[dict]:
    short_text = "Да."
    return [
        {
            "index": 0,
            "text": "Yes.",
            "translated_text": short_text,
            "semantic_text": short_text,
            "start": 0,
            "end": 4000,
            "speaker": "A",
        }
    ]


def _segments_fit() -> list[dict]:
    text = "Привет мир."
    return [
        {
            "index": 0,
            "text": "Hello world.",
            "translated_text": text,
            "semantic_text": text,
            "start": 0,
            "end": 2000,
        }
    ]


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engines.ai_core.timing_agent.agent._MANIFESTS_DIR",
        tmp_path / "manifests",
    )
    monkeypatch.setattr(
        "engines.ai_core.timing_agent.agent._OUTPUT_DIR",
        tmp_path,
    )
    return TimingAgent(output_dir=tmp_path, use_llm=False)


def test_timing_text_only_field_changed(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = _segments_fit()
    state = {"segments": segs, "semantic_agent_status": "success"}
    result = agent.run(manifest, state, "t-timing-only")

    for orig, out in zip(segs, result.updated_state["segments"]):
        assert orig["semantic_text"] == out["semantic_text"]
        assert orig["text"] == out["text"]
        assert orig["start"] == out["start"]
        assert orig["end"] == out["end"]
        assert "timing_text" in out


def test_no_mechanical_truncation(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments_short_slot(), "semantic_agent_status": "success"}
    result = agent.run(manifest, state, "t-no-trunc")
    timing = result.updated_state["segments"][0]["timing_text"]
    assert "..." not in timing
    assert "…" not in timing
    integrity = validate_sentence_integrity(
        state["segments"][0]["semantic_text"], timing
    )
    assert integrity.ok


def test_no_empty_segments(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments_fit(), "semantic_agent_status": "success"}
    result = agent.run(manifest, state, "t-never-empty")
    for seg in result.updated_state["segments"]:
        assert str(seg.get("timing_text") or "").strip()


def test_three_candidates():
    text = "Он сказал что George Smith пошёл домой в настоящее время."
    variants = generate_shorten_candidates(text, tgt_lang="ru")
    assert len(variants) >= 3
    assert "A" in variants and "B" in variants and "C" in variants


def test_shorten_when_overflow():
    text = "Он сказал что George Smith пошёл домой в настоящее время потому что было поздно."
    slot_ms = 800
    predicted = predict_duration_ms(text, "ru")
    assert predicted > slot_ms
    batch = generate_adaptive_candidates(
        text, slot_ms=slot_ms, predicted_ms=predicted, tgt_lang="ru", use_llm=False
    )
    assert batch.rule_rewrite_used
    assert any(len(v) < len(text) for v in batch.variants.values())


def test_expand_when_underflow():
    text = "Да."
    slot_ms = 4000
    predicted = predict_duration_ms(text, "ru")
    assert predicted < slot_ms * 0.82
    batch = generate_adaptive_candidates(
        text, slot_ms=slot_ms, predicted_ms=predicted, tgt_lang="ru", use_llm=False
    )
    assert batch.rule_rewrite_used
    assert any(len(v) > len(text) for v in batch.variants.values())


def test_slot_fit_score():
    perfect = slot_fit_score(2000, 2000)
    assert perfect == 1.0
    bad = slot_fit_score(5000, 2000)
    assert bad < 0.5
    assert timing_score(2000, 2000) == 1.0


def test_micro_stretch_last_resort_only():
    assert not should_apply_micro_stretch(2100, 2000, text_attempts_exhausted=False)
    assert not should_apply_micro_stretch(2050, 2000, text_attempts_exhausted=True)
    assert should_apply_micro_stretch(2080, 2000, text_attempts_exhausted=True)
    assert not should_apply_micro_stretch(2300, 2000, text_attempts_exhausted=True)


def test_fallback_semantic_text():
    semantic = "Очень длинный текст который невозможно уместить " * 5
    retry = apply_retry_policy(
        semantic,
        source="Long source text",
        slot_ms=500,
        tgt_lang="ru",
        use_llm=False,
        max_attempts=1,
    )
    assert retry.text
    assert retry.attempts >= 1


def test_timing_report_json(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments_fit(), "semantic_agent_status": "success"}
    result = agent.run(manifest, state, "t-report")
    report_path = Path(result.updated_state["timing_report_path"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["openddf_agent"] == "Timing/v1"
    assert report["timing_agent_version"] == "1.0"
    assert "per_segment" in report
    assert report["rule_rewrite_used"] is not None


def test_sentence_integrity_validator():
    ok = validate_sentence_integrity("Hello world today.", "Hello world today.")
    assert ok.ok
    bad = validate_sentence_integrity("Hello world today.", "Hello wor...")
    assert not bad.ok
    empty = validate_sentence_integrity("Hello", "")
    assert not empty.ok


def test_gatekeeper_requires_semantic(agent, tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DEBUG_MODE", "0")
    monkeypatch.setattr(
        "engines.ai_core.timing_agent.agent.IS_DEBUG_LEARNING_MODE",
        lambda: False,
    )
    manifest = _manifest(tmp_path)
    state = {"segments": _segments_fit(), "semantic_agent_status": "error"}
    result = agent.run(manifest, state, "t-gate")
    assert result.status == "error"
    assert any("semantic" in e for e in result.errors)
