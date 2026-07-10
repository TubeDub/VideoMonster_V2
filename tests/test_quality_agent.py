"""Tests for TubeDub Quality Agent v1.0."""

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

from engines.ai_core.quality_agent.agent import QualityAgent
from engines.ai_core.quality_agent.decision_engine import decide
from engines.ai_core.quality_agent.scoring import QualityScores, SegmentAuditResult
from engines.ai_core.quality_agent.segment_auditor import audit_segment
from engines.ai_core.quality_agent.smart_router import agent_for_failure, route_and_fix_segment


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


def _good_segments() -> list[dict]:
    return [
        {
            "index": 0,
            "text": "He said that George Smith went home on 12.05.2024.",
            "translated_text": "Он сказал, что George Smith пошёл домой 12.05.2024.",
            "semantic_text": "Он сказал, что George Smith пошёл домой 12.05.2024.",
            "timing_text": "Он сказал, что George Smith пошёл домой 12.05.2024.",
            "grammar_text": "Он сказал, что George Smith пошёл домой 12.05.2024.",
            "start": 0,
            "end": 3500,
            "speaker": "A",
        },
        {
            "index": 1,
            "text": "Wow!!! That is amazing!",
            "translated_text": "Вау! Это удивительно!",
            "semantic_text": "Вау! Это удивительно!",
            "timing_text": "Вау! Это удивительно!",
            "grammar_text": "Вау! Это удивительно!",
            "start": 3500,
            "end": 6000,
            "speaker": "B",
        },
    ]


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engines.ai_core.quality_agent.agent._MANIFESTS_DIR",
        tmp_path / "manifests",
    )
    monkeypatch.setattr(
        "engines.ai_core.quality_agent.agent._OUTPUT_DIR",
        tmp_path,
    )
    return QualityAgent(output_dir=tmp_path)


def test_read_only_no_text_change(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = _good_segments()
    state = {"segments": segs, "grammar_agent_status": "success"}

    ok_audit = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9),
        failure_types=[],
    )
    with patch(
        "engines.ai_core.quality_agent.agent.audit_segment",
        return_value=ok_audit,
    ):
        result = agent.run(manifest, state, "t-read-only")

    text_keys = ("text", "translated_text", "semantic_text", "timing_text", "grammar_text")
    for orig, out in zip(segs, result.updated_state["segments"]):
        for key in text_keys:
            assert orig[key] == out[key]


def _noop_route(seg, *args, **kwargs):
    return seg, None


def test_accept_good_segment(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _good_segments(), "grammar_agent_status": "success"}
    with patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        side_effect=_noop_route,
    ):
        result = agent.run(manifest, state, "t-accept")

    for seg in result.updated_state["segments"]:
        assert seg["quality_decision"] in ("ACCEPT", "WARNING", "FALLBACK")
        assert seg["quality_passed"] is True
        assert seg["quality_scores"]["overall"] > 0


def test_retry_routes_to_timing(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = _good_segments()
    state = {"segments": segs, "grammar_agent_status": "success"}

    audit_fail = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.5, 0.5, 0.5, 0.3, 0.5, 0.5, 0.5, 0.5),
        failure_types=["timing"],
        reasons=["timing_mismatch"],
    )
    audit_ok = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.9, 0.9, 0.9, 0.95, 0.9, 0.9, 0.9, 0.9),
        failure_types=[],
    )

    with patch(
        "engines.ai_core.quality_agent.agent.audit_segment",
        side_effect=[audit_fail, audit_ok, audit_ok],
    ), patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        return_value=(segs[0], "TimingAgent"),
    ) as route_mock:
        agent.run(manifest, state, "t-timing-route")

    assert route_mock.call_args[0][1] == "timing"


def test_retry_routes_to_grammar(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = _good_segments()
    state = {"segments": segs, "grammar_agent_status": "success"}

    audit_fail = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.5, 0.5, 0.4, 0.5, 0.5, 0.5, 0.5, 0.5),
        failure_types=["grammar"],
        reasons=["missing_terminal_punct"],
    )
    audit_ok = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.9, 0.9, 0.95, 0.9, 0.9, 0.9, 0.9, 0.9),
        failure_types=[],
    )

    with patch(
        "engines.ai_core.quality_agent.agent.audit_segment",
        side_effect=[audit_fail, audit_ok, audit_ok],
    ), patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        return_value=(segs[0], "GrammarAgent"),
    ) as route_mock:
        agent.run(manifest, state, "t-grammar-route")

    assert route_mock.call_args[0][1] == "grammar"


def test_max_3_retries(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = _good_segments()
    state = {"segments": segs, "grammar_agent_status": "success"}

    audit_fail = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.4, 0.4, 0.3, 0.4, 0.4, 0.4, 0.4, 0.4),
        failure_types=["grammar"],
        reasons=["bad_grammar"],
    )

    with patch(
        "engines.ai_core.quality_agent.agent.audit_segment",
        return_value=audit_fail,
    ), patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        return_value=(segs[0], "GrammarAgent"),
    ) as route_mock:
        result = agent.run(manifest, state, "t-max-retries")

    assert route_mock.call_count <= 6
    for row in result.metrics["per_segment"]:
        assert row["retry_count"] <= 3


def test_fail_empty_segment(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = [
        {
            "index": 0,
            "text": "",
            "translated_text": "",
            "semantic_text": "",
            "timing_text": "",
            "grammar_text": "",
            "start": 0,
            "end": 1000,
        },
        {
            "index": 1,
            "text": "Bye.",
            "translated_text": "Пока.",
            "semantic_text": "Пока.",
            "timing_text": "Пока.",
            "grammar_text": "Пока.",
            "start": 1000,
            "end": 2000,
        },
    ]
    state = {"segments": segs, "grammar_agent_status": "success"}
    with patch(
        "engines.ai_core.quality_agent.agent.IS_DEBUG_LEARNING_MODE",
        return_value=False,
    ):
        result = agent.run(manifest, state, "t-empty")

    empty_seg = result.updated_state["segments"][0]
    assert empty_seg["quality_decision"] == "FAIL"
    assert empty_seg["quality_passed"] is False


def test_fallback_after_retries(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = _good_segments()
    state = {"segments": segs, "grammar_agent_status": "success"}

    audit_fail = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.65, 0.65, 0.5, 0.7, 0.65, 0.65, 0.65, 0.65),
        failure_types=["grammar"],
        reasons=["stiff_copula"],
    )

    with patch(
        "engines.ai_core.quality_agent.agent.audit_segment",
        return_value=audit_fail,
    ), patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        return_value=(segs[0], "GrammarAgent"),
    ), patch(
        "engines.ai_core.quality_agent.agent.IS_DEBUG_LEARNING_MODE",
        return_value=True,
    ):
        result = agent.run(manifest, state, "t-fallback")

    decisions = {row["decision"] for row in result.metrics["per_segment"]}
    assert "FALLBACK" in decisions or "WARNING" in decisions


def test_entity_check_fails_routes_translation():
    assert agent_for_failure("entity") == "TranslationAgent"
    assert agent_for_failure("terminology") == "TranslationAgent"
    assert agent_for_failure("language") == "TranslationAgent"

    seg = _good_segments()[0]
    manifest = {"source_lang": "en", "target_lang": "ru", "project_uuid": "x"}
    state = {"segments": [seg]}

    with patch(
        "engines.ai_core.quality_agent.retry_orchestrator.rerun_agent_for_segment",
        return_value=seg,
    ) as trans_mock:
        _, agent_name = route_and_fix_segment(
            seg, "entity", manifest, state, "t-entity", segment_index=0
        )
    trans_mock.assert_called_once()
    assert agent_name == "TranslationAgent"


def test_quality_report_json(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _good_segments(), "grammar_agent_status": "success"}
    with patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        side_effect=_noop_route,
    ):
        result = agent.run(manifest, state, "t-report")

    report_path = Path(result.updated_state["quality_report_path"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["openddf_agent"] == "Quality/v1"
    assert report["quality_agent_version"] == "1.0"
    assert "summary" in report
    assert "per_segment" in report


def test_decision_engine_states():
    ok_audit = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9),
        failure_types=[],
    )
    decision = decide(ok_audit, retry_count=0)
    assert decision.decision == "ACCEPT"

    retry_audit = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.5, 0.5, 0.4, 0.5, 0.5, 0.5, 0.5, 0.5),
        failure_types=["grammar"],
        reasons=["bad"],
    )
    decision = decide(retry_audit, retry_count=0)
    assert decision.decision == "RETRY"
    assert decision.failure_type == "grammar"

    decision = decide(retry_audit, retry_count=3, debug_mode=True)
    assert decision.decision in ("FALLBACK", "WARNING")

    critical = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        failure_types=["critical"],
        reasons=["empty_segment"],
        critical=True,
    )
    decision = decide(critical, retry_count=0)
    assert decision.decision == "FAIL"


def test_gatekeeper_requires_grammar(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _good_segments(), "grammar_agent_status": "error"}
    with patch(
        "engines.ai_core.quality_agent.agent.IS_DEBUG_LEARNING_MODE",
        return_value=False,
    ):
        result = agent.run(manifest, state, "t-gate")
    assert result.status == "error"
    assert any("grammar" in e for e in result.errors)


def test_sentence_integrity_blocks():
    manifest = {"source_lang": "en", "target_lang": "ru"}
    segs = [
        {
            "index": 0,
            "text": "This is a long sentence that should not be cut.",
            "translated_text": "Это длинное предложение.",
            "semantic_text": "Это длинное предложение которое не должно обрываться.",
            "timing_text": "Это длинное предложение которое не должно обрываться.",
            "grammar_text": "Это длин...",
            "start": 0,
            "end": 2000,
        }
    ]
    audit = audit_segment(segs[0], all_segments=segs, source_lang="en", target_lang="ru")
    assert "sentence_integrity" in audit.failure_types or not audit.checks["sentence_integrity"]["ok"]


def test_warning_in_debug_mode(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = _good_segments()
    state = {"segments": segs, "grammar_agent_status": "success"}

    audit_warn = SegmentAuditResult(
        index=0,
        scores=QualityScores(0.62, 0.62, 0.55, 0.62, 0.62, 0.62, 0.62, 0.62),
        failure_types=["grammar"],
        reasons=["stiff_copula"],
    )

    with patch(
        "engines.ai_core.quality_agent.agent.audit_segment",
        return_value=audit_warn,
    ), patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        return_value=(segs[0], "GrammarAgent"),
    ), patch(
        "engines.ai_core.quality_agent.agent.IS_DEBUG_LEARNING_MODE",
        return_value=True,
    ):
        result = agent.run(manifest, state, "t-debug-warn")

    assert result.status in ("warning", "success")


def test_quality_passed_flag(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _good_segments(), "grammar_agent_status": "success"}
    with patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        side_effect=_noop_route,
    ):
        result = agent.run(manifest, state, "t-passed")
    for seg in result.updated_state["segments"]:
        assert "quality_passed" in seg
        assert isinstance(seg["quality_passed"], bool)


def test_voice_readiness_triple_consonant(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segs = [
        {
            "index": 0,
            "text": "He met a friend.",
            "translated_text": "Он встттретил друга.",
            "semantic_text": "Он встттретил друга.",
            "timing_text": "Он встттретил друга.",
            "grammar_text": "Он встттретил друга.",
            "start": 0,
            "end": 2500,
        },
        {
            "index": 1,
            "text": "Ok.",
            "translated_text": "Ок.",
            "semantic_text": "Ок.",
            "timing_text": "Ок.",
            "grammar_text": "Ок.",
            "start": 2500,
            "end": 3500,
        },
    ]
    state = {"segments": segs, "grammar_agent_status": "success"}
    with patch(
        "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
        side_effect=_noop_route,
    ):
        result = agent.run(manifest, state, "t-voice")
    scores = result.updated_state["segments"][0]["quality_scores"]
    assert scores["voice_readiness"] < 1.0
