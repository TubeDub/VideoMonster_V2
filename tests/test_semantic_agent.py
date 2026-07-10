"""Tests for TubeDub Semantic Agent v1.0."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.semantic_agent.agent import SemanticAgent
from engines.ai_core.semantic_agent.candidate_selector import (
    generate_candidates,
    select_best_candidate,
)
from engines.ai_core.semantic_agent.llm_rewriter import llm_rewrite
from engines.ai_core.semantic_agent.rule_engine import generate_rule_candidates
from engines.ai_core.semantic_agent.validators.emotion_validator import validate_emotion
from engines.ai_core.semantic_agent.validators.meaning_validator import validate_meaning


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
            "start": 0,
            "end": 3000,
            "speaker": "A",
        },
        {
            "index": 1,
            "text": "Wow!!! That is amazing!",
            "translated_text": "Вау!!! Это удивительно!",
            "start": 3000,
            "end": 6000,
            "speaker": "B",
        },
        {
            "index": 2,
            "text": "I am sad because he left.",
            "translated_text": "Мне грустно потому что он ушёл.",
            "start": 6000,
            "end": 9000,
        },
    ]


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engines.ai_core.semantic_agent.agent._MANIFESTS_DIR",
        tmp_path / "manifests",
    )
    monkeypatch.setattr(
        "engines.ai_core.semantic_agent.agent._OUTPUT_DIR",
        tmp_path,
    )
    return SemanticAgent(output_dir=tmp_path)


def test_only_semantic_text_modified(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "translation_agent_status": "success"}
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(manifest, state, "t-semantic-only")

    for orig, out in zip(state["segments"], result.updated_state["segments"]):
        assert orig["text"] == out["text"]
        assert orig["translated_text"] == out["translated_text"]
        assert orig["start"] == out["start"]
        assert orig["end"] == out["end"]
        assert orig.get("speaker") == out.get("speaker")
        assert "semantic_text" in out


def test_segment_count_unchanged(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "translation_agent_status": "success"}
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(manifest, state, "t-count")
    assert len(result.updated_state["segments"]) == 3


def test_three_candidates_generated():
    variants, _ = generate_candidates(
        "Hello world",
        "Привет мир",
        tgt_lang="ru",
        use_llm=False,
    )
    assert len(variants) >= 3


def test_best_variant_selected():
    variants = generate_rule_candidates(
        "Он сказал что George Smith пошёл домой.",
        source="He said George Smith went home.",
        tgt_lang="ru",
    )
    selection = select_best_candidate(
        "He said George Smith went home.",
        "Он сказал что George Smith пошёл домой.",
        variants,
        tgt_lang="ru",
    )
    assert selection.best.variant in variants or selection.best.variant == "fallback"
    assert selection.best.scores.overall >= 0.0


def test_facts_preserved_meaning_validator():
    source = "John Smith arrived on 15.03.2024 with 42 items."
    translated = "John Smith прибыл 15.03.2024 с 42 items."
    candidate = "John Smith прибыл 15.03.2024 с 42 предметами."
    result = validate_meaning(source, translated, candidate)
    assert result.score >= 0.75
    assert "John Smith" not in result.missing_entities


def test_emotion_preserved():
    source = "Wow!!! That is amazing!"
    translated = "Вау!!! Это удивительно!"
    candidate = "Вау!!! Это потрясающе!"
    result = validate_emotion(source, translated, candidate)
    assert result.score >= 0.6
    assert result.source_emotion in ("excited", "neutral", "question")


def test_llm_fallback_to_rule():
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        text, used = llm_rewrite(
            "Hello",
            "Привет",
            tgt_lang="ru",
        )
    assert text is not None
    assert used is False


def test_never_empty_semantic_text(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segments = [
        {"index": 0, "text": "Hi", "translated_text": "", "start": 0, "end": 1000},
    ]
    state = {"segments": segments, "translation_agent_status": "success"}
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(manifest, state, "t-never-empty")
    assert str(result.updated_state["segments"][0]["semantic_text"]).strip()


def test_no_timing_shortening(agent, tmp_path):
    manifest = _manifest(tmp_path)
    long_trans = "Он сказал, что George Smith пошёл домой 12.05.2024, потому что было поздно."
    state = {
        "segments": [
            {
                "index": 0,
                "text": "He said George Smith went home on 12.05.2024 because it was late.",
                "translated_text": long_trans,
                "start": 0,
                "end": 5000,
            }
        ]
    }
    state["translation_agent_status"] = "success"
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(manifest, state, "t-no-shorten")
    semantic = result.updated_state["segments"][0]["semantic_text"]
    assert len(semantic) >= len(long_trans) * 0.85


def test_semantic_report_json(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "translation_agent_status": "success"}
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(manifest, state, "t-report")

    report_path = Path(result.updated_state["semantic_report_path"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["semantic_agent_version"] == "1.0"
    assert "avg_scores" in report
    assert "variants_generated" in report
    assert "llm_used" in report
    assert "rule_rewrite_used" in report
    assert "per_segment" in report
    assert "decision_log" in report
    assert "execution_time_ms" in report


def test_decision_log(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(), "translation_agent_status": "success"}
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(manifest, state, "t-log")
    assert result.decision_log
    assert any("segment_count=" in line for line in result.decision_log)
    assert any("selected=" in line or "segment_" in line for line in result.decision_log)
