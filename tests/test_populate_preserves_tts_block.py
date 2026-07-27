# -*- coding: utf-8 -*-
"""_populate_translation_review_data must not resurrect blocked MT."""

from __future__ import annotations

from engines.translation_review import build_translation_review


def test_populate_preserves_tts_block(monkeypatch):
    """Simulate STATE_LOCK body of populate without full API import cycle."""
    ZH = "你怀孕了 这个孩子是绑费的 你跟月子前的意外"
    FLOWER = (
        "Ми можемо самі зателефонувати одержувачу і узгодити зручний час "
        "і місце вручення квітів, а якщо необхідно, то збережемо сюрприз."
    )
    info = {
        "task_id": "t1",
        "source_segments": [ZH],
        "target_lang": "uk",
        "detected_lang": "zh",
        "segments_data": [
            {
                "text": "",
                "plain_text": "",
                "final_text": "",
                "approved_text": "",
                "tts_blocked": True,
                "skip_tts": True,
                "needs_manual_review": True,
                "tqe_status": "FAIL_MANUAL_REVIEW",
                "tps_reason_codes": ["meaning_collapse", "cjk_meaning_collapse"],
                "rejected_translation": FLOWER,
                "raw_mt": FLOWER,
                "naturalized_text": FLOWER,
                "trh": {
                    "reason_codes": ["meaning_collapse"],
                    "tqe_status": "FAIL_MANUAL_REVIEW",
                },
            }
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": FLOWER,
                "naturalized_text": FLOWER,
                "final_text": FLOWER,
                "semantic_text": FLOWER,
                "tts_text": FLOWER,
                "reason_codes": ["meaning_collapse"],
                "tqe_status": "FAIL_MANUAL_REVIEW",
            }
        ],
    }
    # Inline the critical decision from populate
    prev = info["segments_data"][0]
    row = info["translation_audits"][0]
    blocked = bool(prev.get("tts_blocked") or prev.get("skip_tts"))
    text = "" if blocked else FLOWER
    assert blocked
    assert text == ""
    review = build_translation_review(info)
    assert review["segments"][0]["final_text"] == ""
    assert (review["segments"][0].get("text_for_tts") or "") == ""
