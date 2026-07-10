"""Tests for TubeDub Grammar Agent v1.0."""

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

from engines.ai_core.grammar_agent.agent import GrammarAgent
from engines.ai_core.grammar_agent.candidate_selector import (
    generate_candidates,
    select_best_candidate,
)
from engines.ai_core.grammar_agent.pronunciation_optimizer import fix_triple_consonants
from engines.ai_core.grammar_agent.rule_engine import (
    apply_grammar_pass,
    fix_punctuation,
    generate_rule_candidates,
)
from engines.ai_core.grammar_agent.scoring import length_ratio, length_within_tolerance
from engines.ai_core.grammar_agent.validators.meaning_preservation import (
    validate_meaning_preservation,
)
from engines.ai_core.translation_agent.validators.entity_validator import extract_entities


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


def _segments() -> list[dict]:
    return [
        {
            "index": 0,
            "text": "He said that George Smith went home on 12.05.2024.",
            "translated_text": "Он сказал что George Smith пошёл домой 12.05.2024.",
            "semantic_text": "Он сказал что George Smith пошёл домой 12.05.2024.",
            "timing_text": "Он сказал что George Smith пошёл домой 12.05.2024.",
            "start": 0,
            "end": 3000,
            "speaker": "A",
        },
        {
            "index": 1,
            "text": "Wow!!! That is amazing!",
            "translated_text": "Вау!!! Это удивительно!",
            "semantic_text": "Вау!!! Это удивительно!",
            "timing_text": "Вау!!! Это удивительно!",
            "start": 3000,
            "end": 6000,
            "speaker": "B",
        },
        {
            "index": 2,
            "text": "I am sad because he left.",
            "translated_text": "Мне грустно потому что он ушёл.",
            "semantic_text": "Мне грустно потому что он ушёл.",
            "timing_text": "Мне грустно потому что он ушёл.",
            "start": 6000,
            "end": 9000,
        },
    ]


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engines.ai_core.grammar_agent.agent._MANIFESTS_DIR",
        tmp_path / "manifests",
    )
    monkeypatch.setattr(
        "engines.ai_core.grammar_agent.agent._OUTPUT_DIR",
        tmp_path,
    )
    return GrammarAgent(output_dir=tmp_path, use_llm=False)


def test_grammar_text_only_changed(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = _segments()
    state = {"segments": segs, "timing_agent_status": "success"}
    result = agent.run(manifest, state, "t-grammar-only")

    for orig, out in zip(segs, result.updated_state["segments"]):
        assert orig["timing_text"] == out["timing_text"]
        assert orig["semantic_text"] == out["semantic_text"]
        assert orig["text"] == out["text"]
        assert orig["start"] == out["start"]
        assert orig["end"] == out["end"]
        assert "grammar_text" in out


def test_no_meaning_change_entities_preserved(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "timing_agent_status": "success"}
    result = agent.run(manifest, state, "t-entities")

    for orig, out in zip(state["segments"], result.updated_state["segments"]):
        source = orig["text"]
        timing = orig["timing_text"]
        grammar = out["grammar_text"]
        meaning = validate_meaning_preservation(source, timing, grammar)
        assert meaning.score >= 0.75
        for ent in extract_entities(timing):
            assert ent in grammar or ent.lower() in grammar.lower()


def test_length_not_changed_significantly(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "timing_agent_status": "success"}
    result = agent.run(manifest, state, "t-length")

    for orig, out in zip(state["segments"], result.updated_state["segments"]):
        timing = orig["timing_text"]
        grammar = out["grammar_text"]
        assert length_within_tolerance(timing, grammar)
        ratio = length_ratio(timing, grammar)
        assert 0.85 <= ratio <= 1.15


def test_three_candidates():
    text = "Он сказал что George Smith пошёл домой."
    variants = generate_rule_candidates(text, tgt_lang="ru")
    assert len(variants) >= 3
    assert "A" in variants and "B" in variants and "C" in variants


def test_pronunciation_triple_consonant_fix():
    raw = "он встттретил друга"
    fixed = fix_triple_consonants(raw)
    assert "\u200b" in fixed or "ттт" not in fixed


def test_fallback_timing_text(agent, tmp_path):
    manifest = _manifest(tmp_path)
    bad_seg = [
        {
            "index": 0,
            "text": "Test.",
            "timing_text": "Тест.",
            "start": 0,
            "end": 1000,
        }
    ]
    state = {"segments": bad_seg, "timing_agent_status": "success"}

    with patch(
        "engines.ai_core.grammar_agent.agent.GrammarAgent._multi_pass",
        return_value="X" * 500,
    ):
        result = agent.run(manifest, state, "t-fallback")

    grammar = result.updated_state["segments"][0]["grammar_text"]
    assert grammar == "Тест."


def test_no_empty_output(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "timing_agent_status": "success"}
    result = agent.run(manifest, state, "t-never-empty")
    for seg in result.updated_state["segments"]:
        assert str(seg.get("grammar_text") or "").strip()


def test_grammar_report_json(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "timing_agent_status": "success"}
    result = agent.run(manifest, state, "t-report")

    report_path = Path(result.updated_state["grammar_report_path"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["openddf_agent"] == "Grammar/v1"
    assert report["grammar_agent_version"] == "1.0"
    assert report["segment_count"] == 3
    assert "avg_scores" in report
    assert "per_segment" in report


def test_gatekeeper_requires_timing(agent, tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DEBUG_MODE", "0")
    monkeypatch.setattr(
        "engines.ai_core.grammar_agent.agent.IS_DEBUG_LEARNING_MODE",
        lambda: False,
    )
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "timing_agent_status": "error"}
    result = agent.run(manifest, state, "t-gate")
    assert result.status == "error"
    assert any("timing_agent_failed" in e for e in result.errors)


def test_rule_engine_ru_punctuation():
    raw = "Он сказал что George Smith пошёл домой"
    out = fix_punctuation(raw, tgt_lang="ru")
    assert ", что" in out or "сказал, что" in out


def test_candidate_selector_picks_best():
    source = "He said George Smith went home."
    timing = "Он сказал что George Smith пошёл домой."
    base = apply_grammar_pass(timing, tgt_lang="ru")
    variants, _ = generate_candidates(source, base, tgt_lang="ru", use_llm=False)
    selection = select_best_candidate(source, timing, variants, tgt_lang="ru")
    assert selection.best.text
    assert len(selection.candidates) >= 1


def test_gatekeeper_requires_timing_text(agent, tmp_path, monkeypatch):
    """v4: empty timing_text is caught by Peer Validation, not grammar gatekeeper."""
    from engines.ai_core.peer_validation import validate_segment_peer_input

    returns = validate_segment_peer_input(
        "grammar",
        {"index": 0, "text": "Hi", "timing_text": ""},
        target_lang="uk",
    )
    assert returns
    assert returns[0].error_code == "missing_timing_text"


def test_gatekeeper_blocks_upstream_error(agent, tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DEBUG_MODE", "0")
    monkeypatch.setattr(
        "engines.ai_core.grammar_agent.agent.IS_DEBUG_LEARNING_MODE",
        lambda: False,
    )
    manifest = _manifest(tmp_path)
    state = {
        "segments": [{"index": 0, "text": "Hi", "timing_text": "Привіт."}],
        "timing_agent_status": "error",
    }
    result = agent.run(manifest, state, "t-timing-error")
    assert result.status == "error"
    assert any("timing_agent_failed" in e for e in result.errors)
