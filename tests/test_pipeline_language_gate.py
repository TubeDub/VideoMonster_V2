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


def test_cjk_source_leak_blocked_for_uk_tts():
    text = (
        "我们陆下八代单纯 此时单保 有惊呀 你怀孕了 陆下有厚了 "
        "要是能一几个男 那就更完美了 这个孩子是绑费的"
    )
    assert detect_segment_language(text, target_lang="uk") == "zh"
    bad, code = is_critical_language_mismatch(text, target_lang="uk")
    assert bad
    assert code == "cjk_in_uk_track"
    issues = validate_segments_target_language(
        [{"text": text, "plain_text": text}],
        source_segments=[text],
        target_lang="uk",
        source_lang="zh",
    )
    assert len(issues) == 1
    assert issues[0]["code"] == "cjk_in_uk_track"


def test_brand_latin_inside_ukrainian_passes():
    text = "iPhone 15 Pro — нова модель від Apple"
    bad, _ = is_critical_language_mismatch(
        text, target_lang="uk", original="iPhone 15 Pro new model from Apple", source_lang="en"
    )
    assert not bad


def test_meaning_collapse_flower_mt_blocked():
    text = "Доставка квітів по Києву за годину."
    src = "你怀孕了"
    issues = validate_segments_target_language(
        [{"text": text, "plain_text": text}],
        source_segments=[src],
        target_lang="uk",
        source_lang="zh",
    )
    assert len(issues) == 1
    assert issues[0]["code"] == "meaning_collapse"
