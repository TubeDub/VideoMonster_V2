"""Tests — translation text consistency across pipeline stages and UI."""

from __future__ import annotations

from engines.translation_review import build_translation_review
from engines.translation_stage_log import text_fingerprint


def test_build_translation_review_uses_final_for_pre_tts():
    info = {
        "source_segments": ["Hello"],
        "target_lang": "uk",
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": "Привіт raw",
                "naturalized_text": "Привіт, світ!",
                "final_text": "Привіт, світ!",
                "tts_text": "Привіт, світ!",
                "validation_warnings": [{"code": "literal_construction", "stage": "final"}],
                "validation_warnings_fingerprint": text_fingerprint(
                    "Привіт raw", "Привіт, світ!", "Привіт, світ!", "Привіт, світ!"
                ),
            }
        ],
        "segments_data": [
            {
                "index": 0,
                "text": "Привіт, світ!",
                "plain_text": "Привіт, світ!",
                "translation_text": "Привіт, світ!",
            }
        ],
    }
    review = build_translation_review(info)
    seg = review["segments"][0]
    assert seg["final_text"] == "Привіт, світ!"
    assert seg["text_for_tts"] == "Привіт, світ!"
    assert seg["ui_matches_tts"] is True
    assert review["qa_mode"] == "advisory"
    assert review["qa_recommendations_applied"] is False


def test_qa_warnings_cached_when_text_unchanged():
    fp = text_fingerprint("raw", "nat", "final", "final")
    info = {
        "source_segments": ["Hi"],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": "raw",
                "naturalized_text": "nat",
                "final_text": "final",
                "validation_warnings": [{"code": "preserved_token", "stage": "final", "tokens": ["X"]}],
                "validation_warnings_fingerprint": fp,
            }
        ],
        "segments_data": [{"index": 0, "text": "final"}],
    }
    review1 = build_translation_review(info)
    review2 = build_translation_review(info)
    assert review1["segments"][0]["warnings"] == review2["segments"][0]["warnings"]
    assert review1["segments"][0]["qa_invoked"] is True


def test_user_edit_invalidates_stale_qa_cache():
    info = {
        "source_segments": ["Hi"],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": "raw",
                "naturalized_text": "nat",
                "final_text": "edited final",
                "user_edited": True,
                "validation_warnings": [{"code": "nonsense", "stage": "final"}],
                "validation_warnings_fingerprint": "stale",
            }
        ],
        "segments_data": [{"index": 0, "text": "edited final"}],
    }
    review = build_translation_review(info)
    audit = info["translation_audits"][0]
    assert review["segments"][0]["final_text"] == "edited final"
    assert audit.get("validation_warnings_fingerprint") != "stale"


def test_review_computes_quality_score_when_audit_missing():
    info = {
        "source_segments": [
            "An 18-year-old boy named George Jr. drove through his hometown.",
        ],
        "source_lang": "en",
        "target_lang": "uk",
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": (
                    "18-річний Джордж молодший поїхав додому на вечерю через рідне місто."
                ),
            }
        ],
        "segments_data": [
            {
                "index": 0,
                "plain_text": (
                    "18-річний Джордж молодший поїхав додому на вечерю через рідне місто."
                ),
            }
        ],
    }
    review = build_translation_review(info)
    seg = review["segments"][0]
    assert seg["quality_score"] > 0
    assert seg["naturalized_text"] == seg["raw_translation"]


def test_translation_stage_log_exports_pipeline_markers():
    from engines.translation_stage_log import log_end, log_segment, log_start

    assert callable(log_start)
    assert callable(log_end)
    assert callable(log_segment)
