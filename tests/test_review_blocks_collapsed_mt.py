# -*- coding: utf-8 -*-
"""Review must not resurrect blocked MT hallucinations into Final/TTS."""

from __future__ import annotations

from engines.translation_review import (
    _resolve_final_text,
    _resolve_text_for_tts,
    build_translation_review,
)

ZH = (
    "我们陆下八代单纯 此时单保 有惊呀 你怀孕了 陆下有厚了 要是能一几个男 那就更完美了 "
    "这个孩子是绑费的 你跟月子前的意外 这孩子是绑费的"
)
FLOWER = (
    "Ми можемо самі зателефонувати одержувачу і узгодити зручний час і місце "
    "вручення квітів, а якщо необхідно, то збережемо сюрприз."
)


def test_tts_blocked_when_pass_carries_collapse_codes():
    """PASS + approved must still block if reason_codes include collapse."""
    from engines.translation_review import _tts_blocked

    BAD = "Той晚上她迎合绑匪，所以这个孩子是绑匪的。"
    seg = {
        "tqe_status": "PASS",
        "approved_text": BAD,
        "final_text": BAD,
        "text": BAD,
        "tps_reason_codes": ["meaning_collapse", "cjk_meaning_collapse"],
        "trh": {
            "reason_codes": ["meaning_collapse", "cjk_meaning_collapse"],
            "tqe_status": "PASS",
        },
    }
    assert _tts_blocked(seg, {}) is True


def test_resolve_final_empty_when_tts_blocked():
    seg = {
        "tts_blocked": True,
        "skip_tts": True,
        "approved_text": "",
        "final_text": "",
        "text": "",
        "naturalized_text": FLOWER,
        "needs_manual_review": True,
        "tqe_status": "FAIL_MANUAL_REVIEW",
        "tps_reason_codes": ["meaning_collapse", "cjk_meaning_collapse"],
        "rejected_translation": FLOWER,
        "source_text": ZH,
    }
    audit = {
        "raw_translation": FLOWER,
        "naturalized_text": FLOWER,
        "approved_text": "",
        "final_text": FLOWER,  # stale audit — must still block
        "tqe_status": "FAIL_MANUAL_REVIEW",
        "reason_codes": ["meaning_collapse", "cjk_meaning_collapse"],
    }
    assert _resolve_final_text(seg, audit) == ""
    assert _resolve_text_for_tts(seg, audit, final="", tts_synthesized=False) == ""


def test_build_review_hides_flower_on_manual_fail():
    info = {
        "source_segments": [ZH],
        "target_lang": "uk",
        "detected_lang": "zh",
        "segments_data": [
            {
                "tts_blocked": True,
                "skip_tts": True,
                "approved_text": "",
                "final_text": "",
                "text": "",
                "plain_text": "",
                "naturalized_text": FLOWER,
                "needs_manual_review": True,
                "tqe_status": "FAIL_MANUAL_REVIEW",
                "tps_reason_codes": ["meaning_collapse"],
                "rejected_translation": FLOWER,
                "trh": {
                    "reason_codes": ["meaning_collapse", "cjk_meaning_collapse"],
                    "tqe_status": "FAIL_MANUAL_REVIEW",
                    "dirty_reasons": ["meaning_collapse"],
                },
            }
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": FLOWER,
                "naturalized_text": FLOWER,
                "final_text": FLOWER,
                "tts_text": FLOWER,
                "approved_text": "",
                "quality_score": 18.0,
                "tqe_status": "FAIL_MANUAL_REVIEW",
                "reason_codes": ["meaning_collapse", "cjk_meaning_collapse"],
                "validation_warnings": [
                    {"code": "meaning_collapse", "stage": "final"},
                    {"code": "cjk_meaning_collapse", "stage": "final"},
                ],
            }
        ],
    }
    review = build_translation_review(info)
    row = review["segments"][0]
    assert row["final_text"] == ""
    assert (row.get("text_for_tts") or row.get("tts_text") or "") == ""
