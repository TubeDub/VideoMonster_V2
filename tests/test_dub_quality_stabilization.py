"""Unit tests for AI Dub Quality Stabilization v1.0 validators and gates.

Golden video (manual E2E): uploads/video_e00b875b63.mp4 (George Lucas dub).
These tests mock translators/ffmpeg — no full pipeline required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.dub_quality_stabilization import (
    GOLDEN_VIDEO_PATH,
    apply_reviewer_repairs,
    audit_segment_for_reviewer,
    basic_meaning_preserved,
    build_dub_quality_report,
    compute_mix_quality_heuristic,
    detect_semantic_compression,
    guarantee_translation_completeness,
    is_sentence_complete,
    is_truncated_sentence,
    write_dub_quality_report_json,
)


# ---- sentence completeness ---------------------------------------------------


def test_is_sentence_complete_accepts_terminal_punctuation():
    ok, issues = is_sentence_complete("Джордж поїхав додому на вечерю.")
    assert ok is True
    assert issues == []


def test_is_sentence_complete_rejects_dangling_fragment():
    ok, issues = is_sentence_complete(
        "Джордж молодший подав заяву на престижну кінотехніку в університеті"
    )
    assert ok is False
    assert "incomplete_sentence" in issues


def test_is_truncated_sentence_detects_ellipsis():
    original = "George was really dreading the long drive."
    clipped = "George was really dreading..."
    assert is_truncated_sentence(original, clipped) is True


def test_detect_semantic_compression_shortening_not_truncation():
    original = "Він не міг не відчувати тривогу перед поїздкою до університету."
    shorter = "Він відчував тривогу перед поїздкою до університету."
    assert detect_semantic_compression(original, shorter) is True
    assert is_truncated_sentence(original, shorter) is False


def test_basic_meaning_preserved_overlap():
    assert basic_meaning_preserved(
        "George Lucas was driving to film school.",
        "Джордж Лукас їхав до кіношколи.",
    )


# ---- reviewer audit ----------------------------------------------------------


def test_audit_segment_for_reviewer_empty_text_routes_translation():
    audit = audit_segment_for_reviewer(
        {"index": 0, "text": "Hello."},
        source_lang="en",
        target_lang="uk",
    )
    assert audit["pass"] is False
    assert "empty_text" in audit["issues"]
    assert audit["route_to"] == "translation"


def test_audit_segment_for_reviewer_complete_uk_passes():
    seg = {
        "index": 0,
        "text": "Hello world, how are you today?",
        "translated_text": "Привіт, світ, як у тебе справи сьогодні?",
        "grammar_text": "Привіт, світ, як у тебе справи сьогодні?",
    }
    audit = audit_segment_for_reviewer(
        seg,
        source_lang="en",
        target_lang="uk",
    )
    assert audit["pass"] is True


def test_apply_reviewer_repairs_retries_empty_translation():
    seg = {"index": 0, "text": "Hello world."}
    audit = {"issues": ["empty_text"], "route_to": "translation"}
    registry = MagicMock()
    with patch(
        "engines.translation_validation.retry_segment_translation",
        return_value=("Привіт, світ.", [{"success": True}]),
    ):
        ok, actions = apply_reviewer_repairs(
            seg,
            audit,
            source_lang="en",
            target_lang="uk",
            registry=registry,
        )
    assert ok is True
    assert seg.get("translated_text") == "Привіт, світ."
    assert "translation_retry_ok" in actions


# ---- translation completeness -----------------------------------------------


def test_guarantee_translation_completeness_recovers_empty():
    segments = [{"index": 0, "text": "Hello world."}]
    registry = MagicMock()
    with patch(
        "engines.translation_validation.retry_segment_translation",
        return_value=("Привіт, світ.", [{"translator": "mock", "success": True}]),
    ):
        fixed, rows = guarantee_translation_completeness(
            segments,
            source_lang="en",
            target_lang="uk",
            registry=registry,
            task_id="test-task",
        )
    assert fixed == 1
    assert segments[0]["translated_text"] == "Привіт, світ."
    assert rows[0]["status"] == "recovered"


def test_guarantee_translation_completeness_records_failure_reason():
    segments = [{"index": 0, "text": "Hello world."}]
    with patch(
        "engines.translation_validation.retry_segment_translation",
        return_value=("", [{"error": "all_failed"}]),
    ):
        fixed, rows = guarantee_translation_completeness(
            segments,
            source_lang="en",
            target_lang="uk",
            task_id="test-task",
        )
    assert fixed == 0
    assert rows[0]["status"] == "failed"
    assert segments[0].get("translation_fallback_reason")


# ---- mix quality heuristic ---------------------------------------------------


def test_compute_mix_quality_heuristic_stem_mix_best():
    score = compute_mix_quality_heuristic(
        separation_success=True,
        used_stem_mix=True,
        music_detected=True,
        fallback_used=False,
    )
    assert score >= 0.9


def test_compute_mix_quality_heuristic_no_separation_lower():
    score = compute_mix_quality_heuristic(
        separation_success=False,
        used_stem_mix=False,
        music_detected=False,
        fallback_used=True,
    )
    assert score < 0.5


# ---- dub_quality_report.json -------------------------------------------------


def test_build_dub_quality_report_metrics(tmp_path):
    info = {
        "target_lang": "uk",
        "source_lang": "en",
        "segments_data": [
            {
                "text": "Hi.",
                "translated_text": "Привіт.",
                "grammar_text": "Привіт.",
                "final_text": "Привіт.",
            }
        ],
        "source_separation": {
            "success": True,
            "accompaniment_path": str(tmp_path / "bg.wav"),
        },
        "final_mix": {
            "used_stem_mix": True,
            "music_detected_in_final": True,
            "fallback_used": False,
        },
    }
    (tmp_path / "bg.wav").write_bytes(b"RIFF")
    report = build_dub_quality_report(info, task_id="t1")
    assert report["summary"]["empty_segments"] == 0
    assert report["summary"]["music_preserved"] is True
    assert report["golden_video"] == GOLDEN_VIDEO_PATH
    assert len(report["per_segment"]) == 1


def test_write_dub_quality_report_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path
    info = {
        "target_lang": "uk",
        "segments_data": [{"grammar_text": "Тест."}],
        "source_separation": {"success": False},
    }
    paths = write_dub_quality_report_json(
        app_dir,
        info,
        task_id="task-abc",
        project_uuid="proj-1",
    )
    assert len(paths) == 2
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["task_id"] == "task-abc"
    tpl = app_dir / "data" / "templates" / "dub_quality_report.json"
    assert tpl.is_file()


# ---- circuit breaker phase reset ---------------------------------------------


def test_reset_circuit_for_phase_clears_global_open():
    from engines.translation_adapt import (
        _LLM_BUDGET_LOCK,
        _llm_budget,
        circuit_open,
        reset_circuit_for_phase,
    )

    with _LLM_BUDGET_LOCK:
        _llm_budget["global_open"] = True
        _llm_budget["global_consec_fail"] = 99
    assert circuit_open() is True
    reset_circuit_for_phase("POST_TTS_QA")
    assert circuit_open() is False


# ---- reviewer agent integration (mocked) -------------------------------------


def test_reviewer_agent_passes_clean_segments():
    from engines.ai_core.reviewer_agent import ReviewerAgent

    manifest = {"project_uuid": "p1", "source_lang": "en", "target_lang": "uk"}
    state = {
        "segments": [
            {
                "index": 0,
                "text": "Hello world, how are you today?",
                "translated_text": "Привіт, світ, як у тебе справи сьогодні?",
                "grammar_text": "Привіт, світ, як у тебе справи сьогодні?",
            }
        ],
        "quality_agent_status": "success",
    }
    agent = ReviewerAgent(output_dir=ROOT / "output")
    result = agent.run(manifest, state, "reviewer-test")
    assert result.status in ("success", "warning")
    assert result.updated_state["segments"][0].get("reviewer_approved") is True
