# -*- coding: utf-8 -*-
"""_segments_data_entries must not resurrect flower MT into TTS rows."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_segments_data_entries():
    """Load helper without importing full Flask app stack."""
    path = ROOT / "api" / "auto_dub_api.py"
    src = path.read_text(encoding="utf-8")
    # Extract function via exec of isolated copy — import module is heavy; call via import.
    spec = importlib.util.spec_from_file_location("auto_dub_api_test", path)
    # Prefer direct import of function after minimal path setup
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # Import only the function by compiling a stub — fall back to real import
    from api.auto_dub_api import _segments_data_entries

    return _segments_data_entries


def test_blocked_segment_stays_empty():
    ZH = "你怀孕了 这个孩子是绑费的"
    FLOWER = (
        "Ми можемо самі зателефонувати одержувачу і узгодити зручний час "
        "і місце вручення квітів, а якщо необхідно, то збережемо сюрприз."
    )
    fn = _load_segments_data_entries()
    info = {
        "source_word_maps": [],
        "translation_audits": [
            {
                "index": 0,
                "final_text": FLOWER,
                "naturalized_text": FLOWER,
                "raw_translation": FLOWER,
                "reason_codes": ["meaning_collapse"],
                "tqe_status": "FAIL_MANUAL_REVIEW",
            }
        ],
        "segments_data": [
            {
                "index": 0,
                "text": "",
                "approved_text": "",
                "tts_blocked": True,
                "skip_tts": True,
                "needs_manual_review": True,
                "tqe_status": "FAIL_MANUAL_REVIEW",
                "tps_reason_codes": ["meaning_collapse"],
                "rejected_translation": FLOWER,
                "trh": {"reason_codes": ["meaning_collapse"], "tqe_status": "FAIL_MANUAL_REVIEW"},
            }
        ],
    }
    out = fn([""], info)
    assert out[0]["text"] == ""
    assert out[0]["plain_text"] == ""
    assert out[0]["translation_text"] == ""
    assert out[0]["final_text"] == ""
    assert out[0]["tts_blocked"] is True
    assert FLOWER not in (out[0].get("text") or "")


def test_pass_plus_approved_collapse_still_blocked():
    """_tmp_3333: TPS PASS + approved_text must not resurrect collapse codes."""
    ZH = "你怀孕了 这个孩子是绑费的"
    BAD = (
        "Ми в родині осма покоління. Той晚上她迎合绑匪，所以这个孩子是绑匪的。"
    )
    fn = _load_segments_data_entries()
    info = {
        "source_word_maps": [],
        "translation_audits": [
            {
                "index": 0,
                "final_text": BAD,
                "approved_text": BAD,
                "reason_codes": ["meaning_collapse", "cjk_meaning_collapse"],
                "tqe_status": "PASS",
            }
        ],
        "segments_data": [
            {
                "index": 0,
                "text": BAD,
                "approved_text": BAD,
                "tqe_status": "PASS",
                "tps_path": "retry",
                "tps_reason_codes": [
                    "dirty_mt_noop",
                    "meaning_collapse",
                    "cjk_meaning_collapse",
                ],
                "trh": {
                    "reason_codes": [
                        "dirty_mt_noop",
                        "meaning_collapse",
                        "cjk_meaning_collapse",
                    ],
                    "tqe_status": "PASS",
                    "approved": BAD,
                },
            }
        ],
    }
    out = fn([BAD], info)
    assert out[0]["text"] == ""
    assert out[0]["approved_text"] == ""
    assert out[0]["final_text"] == ""
    assert out[0]["tts_blocked"] is True
    assert out[0]["skip_tts"] is True
    assert "FAIL" in str(out[0].get("tqe_status") or "").upper()
    assert ZH not in (out[0].get("text") or "")


def test_resolve_tts_respects_block():
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts

    FLOWER = "Місце вручення квітів, збережемо сюрприз."
    assert (
        resolve_segment_text_for_tts(
            {
                "tts_blocked": True,
                "text": FLOWER,
                "final_text": FLOWER,
                "semantic_text": FLOWER,
            }
        )
        == ""
    )


def test_slot_budget_blocks_tts_flag():
    from engines.pipeline_integrity.slot_budget import segment_tts_allowed

    assert segment_tts_allowed({"tts_blocked": True, "text": "x"}) is False
    assert segment_tts_allowed({"skip_tts": True, "text": "x"}) is False
    assert (
        segment_tts_allowed(
            {"tqe_status": "FAIL_MANUAL_REVIEW", "approved_text": "", "text": "x"}
        )
        is False
    )
    assert segment_tts_allowed({"text": "ok", "approved_text": "ok"}) is True
