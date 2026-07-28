# -*- coding: utf-8 -*-
"""Stage 4: Review text == TTS; paraphrase fit without mid-thought cuts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_stamp_final_tts_text_authority():
    from engines.tts_text_authority import (
        resolve_final_tts_text,
        stamp_final_tts_text,
        text_hash,
    )

    seg = {"translated_text": "RAW MT blob long"}
    out = stamp_final_tts_text(seg, "Фінальний текст для озвучки.")
    assert out == "Фінальний текст для озвучки."
    assert seg["final_tts_text"] == out
    assert seg["spoken_text_source"] == "final_tts_text"
    assert seg["tts_text_hash"] == text_hash(out)
    assert resolve_final_tts_text(seg) == out


def test_resolve_tts_prefers_final_tts_text():
    from engines.pipeline_integrity.tts_segment_fields import (
        resolve_segment_text_for_tts,
        resolve_tts_input_text,
    )

    seg = {
        "final_tts_text": "LOCKED",
        "text": "STALE",
        "semantic_text": "STALE2",
    }
    assert resolve_segment_text_for_tts(seg) == "LOCKED"
    group = {"final_tts_text": "GROUP LOCKED", "text": "other", "plain_text": ""}
    assert resolve_tts_input_text(group) == "GROUP LOCKED"


def test_fit_refuses_mid_thought_cut():
    from engines.text_slot_fit import fit_text_to_slot

    # Classic bad cut pattern from Review
    long_uk = (
        "Наприклад, чому ви не можете зосередитися на цьому й застосувати його "
        "до інших речей, щоб ми отримали вашу справжню роботу. І тому практично "
        "кожна вечеря в ці дні перетворювалася на величезну суперечку між батьком і сином."
    )
    fit = fit_text_to_slot(long_uk, 3500, "uk")
    assert not fit.text.rstrip(".…").endswith("застосувати")
    assert "й застосувати." not in fit.text
    # Either shortened to a complete thought or left full (mild overflow OK)
    assert fit.meaning_truncated is False or fit.text == long_uk


def test_fit_keeps_complete_sentences():
    from engines.text_slot_fit import _is_complete_thought, fit_text_to_slot

    text = (
        "Це за винятком автомобілів. І в той момент батько купив йому Fiat. "
        "Але він не зрозумів одержимість сина."
    )
    fit = fit_text_to_slot(text, 4000, "uk")
    assert _is_complete_thought(fit.text) or fit.text == text
    assert not fit.meaning_truncated


def test_fit_rejects_dangling_despite_clause():
    from engines.text_slot_fit import _is_complete_thought

    bad = (
        "Це за винятком автомобілів. І в той момент його батько купив йому "
        "маленький італійський автомобіль під назвою «фіат». "
        "Але його батько, незважаючи на те."
    )
    assert not _is_complete_thought(bad)
    assert not _is_complete_thought("Тож Джордж-молодший вирішив.")


def test_freeze_prefers_final_tts_text():
    from engines.tts_review_align import freeze_spoken_to_review_final

    sd = [
        {
            "final_tts_text": "SHORT FITTED.",
            "text": "LONG STALE SEMANTIC BLOB HERE.",
            "final_text": "LONG STALE SEMANTIC BLOB HERE.",
        }
    ]
    audits = [
        {
            "index": 0,
            "final_text": "LONG STALE SEMANTIC BLOB HERE.",
            "semantic_text": "LONG STALE SEMANTIC BLOB HERE.",
        }
    ]
    out = freeze_spoken_to_review_final(
        ["LONG STALE SEMANTIC BLOB HERE."], sd, audits
    )
    assert out[0] == "SHORT FITTED."
    assert sd[0]["text"] == "SHORT FITTED."
    assert sd[0]["final_tts_text"] == "SHORT FITTED."
    assert audits[0]["final_text"] == "SHORT FITTED."


def test_stamp_overwrites_audit_semantic():
    from engines.tts_text_authority import stamp_final_tts_text

    seg = {}
    audit = {
        "index": 0,
        "final_text": "LONG",
        "semantic_text": "LONG SEMANTIC",
        "semantic_engine_text": "LONG SEMANTIC",
    }
    stamp_final_tts_text(seg, "SHORT.", audit=audit, source="test")
    assert audit["final_text"] == "SHORT."
    assert audit["semantic_text"] == "SHORT."
    assert audit["final_tts_text"] == "SHORT."


def test_align_restores_fitted_snapshot():
    from engines.tts_review_align import align_info_for_translation_review

    info = {
        "final_tts_locked": True,
        "fitted_tts_texts": ["FIT A.", "FIT B."],
        "source_segments": ["a", "b"],
        "translation_audits": [
            {
                "index": 0,
                "final_text": "LONG A",
                "semantic_text": "LONG A",
            },
            {
                "index": 1,
                "final_text": "LONG B",
                "semantic_text": "LONG B",
            },
        ],
        "segments_data": [
            {"text": "LONG A", "final_tts_text": "LONG A"},
            {"text": "LONG B", "final_tts_text": "LONG B"},
        ],
    }
    align_info_for_translation_review(info)
    assert info["segments_data"][0]["final_tts_text"] == "FIT A."
    assert info["segments_data"][1]["text"] == "FIT B."
    assert info["translation_audits"][0]["semantic_text"] == "FIT A."
