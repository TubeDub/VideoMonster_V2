"""Professional Translation Review diagnostics (TZ tasks 1–7, 10, 12)."""

from __future__ import annotations

from engines.translation_review import build_translation_review
from engines.translation_review_diagnostics import (
    build_segment_diagnostics,
    fill_status,
    overflow_text_split,
)
from engines.tts_speech_end import analyze_speech_end


def test_fill_status_bands():
    assert fill_status(90) == "green"
    assert fill_status(97) == "yellow"
    assert fill_status(102) == "orange"
    assert fill_status(110) == "red"


def test_overflow_text_split_marks_tail():
    text = "А " * 40 + "хвіст який не вміщується"
    split = overflow_text_split(text, slot_ms=1000, tts_ms=1500)
    assert split["fits"]
    assert split["overflow"]
    assert split["overflow_char_start"] < len(text)


def test_overflow_no_split_when_fits():
    text = "Короткий текст"
    split = overflow_text_split(text, slot_ms=5000, tts_ms=1000)
    assert split["overflow"] == ""
    assert split["fits"] == text


def test_build_segment_diagnostics_has_pro_fields():
    diag = build_segment_diagnostics(
        seg={"adaptation_stages": ["dsal_compress", "trim_silence"], "semantic_adapted": True},
        audit={"naturalizer_applied": True, "naturalizer_reasons": ["literary_uk"]},
        text="Довгий текст " * 20,
        original="Long text " * 20,
        slot_ms=2000,
        tts_ms=2600,
        tgt_lang="uk",
        warnings=[{"code": "entity_missing", "stage": "final"}],
    )
    assert diag["fill_pct"] > 100
    assert diag["fill_status"] in ("orange", "red")
    assert diag["text_overflow"]
    assert "DSAL" in diag["algorithms"] or "Trim Silence" in diag["algorithms"]
    assert diag["entity_risk"] is True
    assert "original_end_ms" in diag["speech_end"]


def test_review_payload_includes_diagnostics():
    review = build_translation_review(
        {
            "source_lang": "en",
            "target_lang": "uk",
            "source_segments": ["George drove home for dinner."],
            "segments_data": [
                {
                    "slot_ms": 3000,
                    "playback_duration": 2800,
                    "text": "Джордж їхав додому на вечерю.",
                    "final_text": "Джордж їхав додому на вечерю.",
                    "adaptation_stages": ["pause_optimization"],
                }
            ],
            "translation_audits": [
                {
                    "index": 0,
                    "whisper_text": "George drove home for dinner.",
                    "raw_translation": "Джордж їхав додому.",
                    "naturalized_text": "Джордж їхав додому на вечерю.",
                    "final_text": "Джордж їхав додому на вечерю.",
                    "quality_score": 82,
                    "naturalizer_applied": True,
                    "semantic_adapted": True,
                }
            ],
        }
    )
    seg = review["segments"][0]
    assert "diagnostics" in seg
    assert "fill_pct" in seg
    assert "quality_breakdown" in seg
    assert "algorithms" in seg
    assert "speech_end" in seg
    assert "voice_finished_naturally" in seg


def test_analyze_speech_end_duration_fallback():
    info = analyze_speech_end(None, slot_ms=1000, playback_ms=1400)
    assert info["voice_truncated"] is True
    assert info["voice_finished_naturally"] is False


def test_usc_phonetic_satisfies_entity():
    from engines.translation_quality import missing_preserved_tokens

    missing = missing_preserved_tokens(
        "University of Southern California",
        "програма кінематографії в Ю Ес Сі",
    )
    assert "University" not in missing
    assert "Southern" not in missing
    assert "California" not in missing
