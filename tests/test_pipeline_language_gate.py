"""Tests for segment language validation gate."""

from engines.pipeline_language_gate import (
    detect_segment_language,
    is_critical_language_mismatch,
    validate_segments_target_language,
)


def test_detect_english_in_ukrainian_track():
    text = "that Джордж-молодший. was ejected from the car but he had survived."
    assert detect_segment_language(text, target_lang="uk") == "en"
    bad, code = is_critical_language_mismatch(text, target_lang="uk")
    assert bad
    assert code == "english_in_uk_track"


def test_pure_ukrainian_passes():
    text = "Джорджа-молодшого викинули з машини, але він вижив."
    bad, _ = is_critical_language_mismatch(text, target_lang="uk")
    assert not bad


def test_validate_segments_stops_on_mismatch():
    segments = [
        {"text": "Чистий український текст.", "plain_text": "Чистий український текст."},
        {"text": "That was wrong.", "plain_text": "That was wrong."},
    ]
    issues = validate_segments_target_language(
        segments,
        source_segments=["ok", "That was wrong."],
        target_lang="uk",
    )
    assert len(issues) == 1
    assert issues[0]["index"] == 1
