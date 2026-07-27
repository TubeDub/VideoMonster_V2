"""Tests for translation → voice preparation state passing and LANGUAGE_MISMATCH recovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.voice_preparation_agent.agent import VoicePreparationAgent
from engines.translation_validation import (
    build_validation_row,
    build_validation_rows_from_info,
    recover_mismatched_segments,
    resolve_final_text,
    resolve_voice_input,
    sync_final_text_to_task_info,
    validate_segment_for_target,
    write_translation_validation_json,
)


def test_translation_writes_to_project_state():
    info = {
        "source_segments": ["Hello world."],
        "target_lang": "uk",
        "source_lang": "en",
        "segments_data": [
            {
                "index": 0,
                "original_text": "Hello world.",
                "translated_text": "Привіт, світ.",
                "grammar_text": "Привіт, світ.",
            }
        ],
        "translation_audits": [{"index": 0, "whisper_text": "Hello world."}],
    }
    sync_final_text_to_task_info(info)
    audit = info["translation_audits"][0]
    assert audit["final_text"] == "Привіт, світ."
    assert audit["tts_text"] == "Привіт, світ."
    assert info["segments_data"][0]["final_text"] == "Привіт, світ."


def test_voice_prep_uses_final_text_not_original():
    agent = VoicePreparationAgent()
    state = {
        "segments": [
            {
                "index": 0,
                "text": "An 18-year-old boy named George Jr.",
                "translated_text": "Вісімнадцятирічний хлопець на ім'я Джордж-молодший.",
                "grammar_text": "Вісімнадцятирічний хлопець на ім'я Джордж-молодший.",
            }
        ]
    }
    result = agent.run({}, state, "test-task")
    seg = result.updated_state["segments"][0]
    assert "George Jr" not in seg["voice_input"]
    assert "Джордж" in seg["voice_input"]


def test_uk_target_rejects_english_text():
    v = validate_segment_for_target(
        "An 18-year-old boy named George Jr. drove through his hometown.",
        target_lang="uk",
        original="An 18-year-old boy named George Jr. drove through his hometown.",
    )
    assert v["fail"]
    # Dominant-script gate may report latin_in_uk_track; legacy english_in_uk_track
    # still valid when function-word heuristics fire first.
    assert v["reason"] in {
        "english_in_uk_track",
        "latin_in_uk_track",
        "no_cyrillic_in_target_track",
        "source_script_leak_latin",
    }


def test_language_mismatch_triggers_retry():
    info = {
        "source_segments": [
            "An 18-year-old boy named George Jr. drove through his hometown."
        ],
        "source_lang": "en",
        "target_lang": "uk",
        "capability_matrix": {},
        "segments_data": [
            {
                "index": 0,
                "original_text": "An 18-year-old boy named George Jr. drove through his hometown.",
                "text": "An 18-year-old boy named George Jr. drove through his hometown.",
                "plain_text": "An 18-year-old boy named George Jr. drove through his hometown.",
                "grammar_text": "An 18-year-old boy named George Jr. drove through his hometown.",
            }
        ],
        "translation_audits": [],
    }
    issues = [
        {
            "index": 0,
            "code": "english_in_uk_track",
            "detected_lang": "en",
            "target_lang": "uk",
        }
    ]

    mock_result = MagicMock()
    mock_result.translated = "Вісімнадцятирічний хлопець на ім'я Джордж-молодший їхав через рідне місто."
    mock_result.translator_name = "deep-translator"
    mock_result.success = True
    mock_result.confidence = 0.9
    mock_result.error = None
    mock_result.decision_log = []

    with patch(
        "engines.ai_core.translation_agent.retry_policy.translate_with_fallback",
        return_value=mock_result,
    ):
        fixed, still_bad = recover_mismatched_segments(
            info,
            issues,
            task_id="retry-test",
            source_lang="en",
            target_lang="uk",
            app_dir=ROOT,
        )

    assert fixed == 1
    assert not still_bad
    assert "Джордж" in info["segments_data"][0]["grammar_text"]
    assert info["translation_audits"][0]["final_text"] == info["segments_data"][0]["grammar_text"]


def test_validation_json_created(tmp_path):
    rows = [
        build_validation_row(
            0,
            {
                "translated_text": "Привіт.",
                "grammar_text": "Привіт.",
                "semantic_text": "Привіт.",
            },
            audit={"raw_translation": "Привіт."},
            original_text="Hello.",
            target_lang="uk",
            source_lang="en",
        )
    ]
    paths = write_translation_validation_json(
        "json-test-task",
        rows,
        project_uuid="proj-123",
        app_dir=tmp_path,
    )
    assert len(paths) == 2
    diag = tmp_path / "output" / "diagnostics" / "json-test-task" / "translation_validation.json"
    assert diag.is_file()
    payload = json.loads(diag.read_text(encoding="utf-8"))
    assert payload["segment_count"] == 1
    assert payload["segments"][0]["validation_result"]["pass"] is True
    assert payload["segments"][0]["voice_input"] == "Привіт."


def test_resolve_voice_input_skips_original_text_field():
    seg = {
        "text": "English original that must not leak.",
        "original_text": "English original that must not leak.",
        "grammar_text": "Український переклад.",
    }
    assert resolve_voice_input(seg) == "Український переклад."
    assert resolve_final_text(seg) == "Український переклад."


def test_build_validation_rows_from_info(tmp_path):
    info = {
        "source_segments": ["Hi"],
        "source_lang": "en",
        "target_lang": "uk",
        "segments_data": [{"grammar_text": "Привіт.", "translated_text": "Привіт."}],
        "translation_audits": [],
    }
    rows = build_validation_rows_from_info(info)
    assert len(rows) == 1
    assert rows[0]["original_text"] == "Hi"
    assert rows[0]["grammar_output"] == "Привіт."
