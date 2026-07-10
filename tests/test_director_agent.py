"""Tests for TubeDub Director Agent v1.0."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.director_agent import DirectorAgent
from engines.ai_core.director_agent.brief_validator import repair_brief
from engines.ai_core.director_agent.context_window import build_context_window
from engines.ai_core.director_agent.creative_brief import CreativeBrief
from engines.ai_core.director_agent.defaults import DEFAULT_BRIEF_VALUES
from engines.ai_core.director_agent.rule_analyzer import analyze_segment_rules
from engines.ai_core.orchestrator import AICoreOrchestrator
from engines.ai_core.semantic_agent.agent import _brief_semantic_threshold
from engines.ai_core.translation_agent.agent import _brief_translation_threshold


@pytest.fixture
def manifest(tmp_path):
    uid = "dir-test-uuid"
    mdir = tmp_path / "manifests" / uid
    mdir.mkdir(parents=True)
    data = {
        "project_uuid": uid,
        "planner_version": "3.0",
        "source_lang": "en",
        "target_lang": "ru",
    }
    path = mdir / "project_manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


@pytest.fixture
def director(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr("engines.ai_core.director_agent.agent._MANIFESTS_DIR", out / "manifests")
    monkeypatch.setattr("engines.ai_core.director_agent.agent._OUTPUT_DIR", out)
    return DirectorAgent(output_dir=out)


@pytest.fixture
def sample_segments():
    return [
        {
            "index": 0,
            "text": "Hello, how are you?",
            "start": 0,
            "end": 2500,
            "speaker": "A",
        },
        {
            "index": 1,
            "text": "I am fine, thanks!",
            "start": 2500,
            "end": 5000,
            "speaker": "B",
        },
        {
            "index": 2,
            "text": "That's great!",
            "start": 5000,
            "end": 7000,
            "speaker": "A",
        },
    ]


def test_read_only_no_text_change(director, manifest, sample_segments):
    before = [dict(s) for s in sample_segments]
    with patch(
        "engines.ai_core.director_agent.agent.analyze_segment_llm",
        return_value=(None, False),
    ):
        result = director.run(manifest, {"segments": sample_segments}, "t-readonly")
    after = result.updated_state["segments"]
    for orig, seg in zip(before, after):
        assert seg["text"] == orig["text"]
        assert seg.get("start") == orig.get("start")
        assert seg.get("end") == orig.get("end")
        assert seg.get("speaker") == orig.get("speaker")


def test_brief_created_every_segment(director, manifest, sample_segments):
    with patch(
        "engines.ai_core.director_agent.agent.analyze_segment_llm",
        return_value=(None, False),
    ):
        result = director.run(manifest, {"segments": sample_segments}, "t-briefs")
    segments = result.updated_state["segments"]
    assert len(segments) == 3
    for seg in segments:
        brief = seg.get("creative_brief")
        assert isinstance(brief, dict)
        assert brief.get("segment_id") == seg["index"]
        assert brief.get("speaker_id")


def test_no_empty_required_fields(director, manifest, sample_segments):
    with patch(
        "engines.ai_core.director_agent.agent.analyze_segment_llm",
        return_value=(None, False),
    ):
        result = director.run(manifest, {"segments": sample_segments}, "t-required")
    for seg in result.updated_state["segments"]:
        brief = seg["creative_brief"]
        assert brief["speaker_id"]
        assert brief["language"]
        assert brief["emotion"]
        assert brief["speech_style"]
        assert brief["utterance_goal"]
        assert brief["maximum_duration_ms"] >= 1
        assert brief["meaning_priority"] >= DEFAULT_BRIEF_VALUES["meaning_priority"] - 0.01


def test_fallback_rule_engine(director, manifest, sample_segments):
    with patch(
        "engines.ai_core.director_agent.agent.analyze_segment_llm",
        return_value=(None, False),
    ):
        result = director.run(manifest, {"segments": sample_segments}, "t-fallback")
    brief = result.updated_state["segments"][0]["creative_brief"]
    reasons = brief.get("decision_reasons") or []
    assert "rule_engine" in reasons
    assert result.metrics.get("rule_only_count", 0) >= 1


def test_context_uses_neighbors(manifest, sample_segments):
    ctx = build_context_window(sample_segments, 1, window=2)
    assert ctx["prev_text"] == "Hello, how are you?"
    assert ctx["next_text"] == "That's great!"
    assert len(ctx["prev_texts"]) >= 1
    assert len(ctx["next_texts"]) >= 1

    rule = analyze_segment_rules(
        sample_segments[1],
        segments=sample_segments,
        index=1,
        language="en",
        window=2,
    )
    assert "rule_engine" in rule["decision_reasons"]
    assert rule.get("_context_used") is True


def test_llm_structured_not_chat(director, manifest, sample_segments):
    captured: dict[str, object] = {}

    def _fake_llm(seg, *, context, language, task_id, segment_idx, timeout=20.0):
        captured["task_id"] = task_id
        captured["segment_idx"] = segment_idx
        return (
            {
                "emotion": "Excited",
                "speech_style": "conversational",
                "speaking_speed": "fast",
                "decision_reasons": ["llm_structured_brief"],
            },
            True,
        )

    with patch(
        "engines.ai_core.director_agent.agent.analyze_segment_llm",
        side_effect=_fake_llm,
    ):
        result = director.run(manifest, {"segments": sample_segments[:1]}, "t-llm")

    assert captured.get("task_id") == "t-llm"
    brief = result.updated_state["segments"][0]["creative_brief"]
    assert brief["emotion"] == "Excited"
    assert "llm_structured_brief" in brief["decision_reasons"]
    assert result.metrics.get("llm_used_count", 0) >= 1


def test_downstream_agents_read_brief():
    seg = {
        "creative_brief": {
            "literal_phrasing_importance": 0.9,
            "formality": 0.8,
            "adaptation_priority": 0.85,
            "deep_semantic_adaptation_needed": True,
            "emotion": "Happy",
            "meaning_priority": 0.96,
            "naturalness_priority": 0.9,
            "lip_sync_priority": 0.7,
        }
    }
    tr_threshold = _brief_translation_threshold(seg, 0.75)
    assert tr_threshold > 0.75
    sem_threshold = _brief_semantic_threshold(seg, 0.85)
    assert sem_threshold < 0.85


def test_orchestrator_order_planner_director_translation():
    names = [name for name, _, _ in AICoreOrchestrator.AGENT_CHAIN]
    assert names.index("planner") < names.index("director")
    assert names.index("director") < names.index("translation")
    director_timeout = next(t for n, _, t in AICoreOrchestrator.AGENT_CHAIN if n == "director")
    assert director_timeout == 60


def test_director_report_json(director, manifest, sample_segments):
    with patch(
        "engines.ai_core.director_agent.agent.analyze_segment_llm",
        return_value=(None, False),
    ):
        result = director.run(manifest, {"segments": sample_segments}, "t-report")
    report_path = Path(result.updated_state["director_report_path"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["openddf_agent"] == "Director/v1"
    assert report["director_agent_version"] == "1.0"
    assert report["segment_count"] == 3
    assert len(report["per_segment"]) == 3


def test_pipeline_continues_on_llm_fail(director, manifest, sample_segments):
    with patch(
        "engines.ai_core.director_agent.agent.analyze_segment_llm",
        return_value=(None, False),
    ):
        result = director.run(manifest, {"segments": sample_segments}, "t-continue")
    assert result.status in ("success", "warning")
    assert len(result.updated_state["segments"]) == 3
    assert result.metrics.get("rule_only_count", 0) == 3


def test_question_segment_goal(director, manifest):
    segments = [{"index": 0, "text": "What is your name?", "start": 0, "end": 2000}]
    with patch(
        "engines.ai_core.director_agent.agent.analyze_segment_llm",
        return_value=(None, False),
    ):
        result = director.run(manifest, {"segments": segments}, "t-question")
    brief = result.updated_state["segments"][0]["creative_brief"]
    assert brief["utterance_goal"] == "question"


def test_brief_validator_clamps_preferred_duration():
    repaired = repair_brief(
        {
            "segment_id": 0,
            "speaker_id": "A",
            "language": "en",
            "maximum_duration_ms": 2000,
            "preferred_duration_ms": 5000,
        }
    )
    assert repaired["preferred_duration_ms"] <= repaired["maximum_duration_ms"]


def test_director_api_module_importable():
    from api.director_api import bp

    assert bp.name == "director_api"


def test_creative_brief_from_dict_defaults():
    brief = CreativeBrief.from_dict({"segment_id": 1, "speaker_id": "x", "language": "ru"})
    assert brief.emotion == DEFAULT_BRIEF_VALUES["emotion"]
    assert brief.meaning_priority == DEFAULT_BRIEF_VALUES["meaning_priority"]
