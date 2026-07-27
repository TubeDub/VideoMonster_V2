# -*- coding: utf-8 -*-
"""Regression: debleeded Final must not be reclaimed by a shared MT blob."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts
from engines.translation_validation import (
    is_shared_mt_blob_reclaim,
    prefer_semantic_authority,
    resolve_post_quality_text,
)
from engines.tts_review_align import align_info_for_translation_review
from engines.tts_text_guard import repair_neighbor_bleed


DINNER = (
    "І тому практично кожна вечеря в ці дні перетворювалася на величезну "
    "суперечку між батьком і сином"
)
CRASH = (
    "І ось Джордж, він підійшов до цього перехрестя, де воно було прямо біля "
    "його дому, і він почав повертати, коли почув цей дуже гучний вереск, а "
    "потім усе пішло. Через два тижні Джордж-молодший лежав на лікарняному "
    "ліжку у відділенні інтенсивної терапії місцевої лікарні"
)
BLOB = f"{DINNER}. {CRASH}."

USC_A = "Насправді Джордж-молодший подав заявку на престижну програму кінематографії в USC,"
USC_B = "але після того, як відправив свою заявку, був майже впевнений, що його не приймуть"
USC_BLOB = (
    "Насправді Джордж-молодший подав заявку на престижну програму кінематографії "
    "в USC, але після того, як відправив свою заявку, він був майже впевнений, "
    "що його не приймуть."
)


def test_is_shared_mt_blob_reclaim_detects_superstring():
    assert is_shared_mt_blob_reclaim(DINNER, BLOB)
    assert not is_shared_mt_blob_reclaim(BLOB, DINNER)
    assert not is_shared_mt_blob_reclaim(DINNER, DINNER)


def test_prefer_semantic_refuses_blob_when_owned_split_matches_raw():
    assert not prefer_semantic_authority(
        semantic=BLOB,
        candidate=DINNER,
        raw_mt=DINNER,
        source="And so basically every dinner these days it became this huge argument.",
    )
    owned = resolve_post_quality_text(
        {
            "final_text": DINNER,
            "voice_input": DINNER,
            "semantic_engine_text": BLOB,
            "original_text": "Dinner argument between father and son.",
        },
        {"raw_translation": DINNER},
    )
    assert owned == DINNER
    assert "перехрестя" not in owned


def test_resolve_tts_prefers_owned_split_over_semantic_blob():
    seg = {
        "final_text": USC_B,
        "translated_text": USC_B,
        "semantic_engine_text": USC_BLOB,
        "semantic_text": USC_BLOB,
        "voice_input": USC_BLOB,
        "raw_translation": USC_BLOB,
        "original_text": "but after sending off his application, he was pretty sure he would not get in.",
    }
    text = resolve_segment_text_for_tts(seg)
    assert text.rstrip(".!?…") == USC_B.rstrip(".!?…")
    assert "Насправді" not in text


def test_align_snaps_stale_semantic_after_debleed():
    info = {
        "source_segments": [
            "And so basically every dinner these days it became this huge argument between father and son.",
            "And so George, he came to this intersection near his home and heard a screech. Two weeks later he was in hospital.",
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": BLOB,
                "naturalized_text": BLOB,
                "final_text": BLOB,
                "tts_text": BLOB,
                "semantic_text": BLOB,
                "semantic_engine_text": BLOB,
            },
            {
                "index": 1,
                "raw_translation": BLOB,
                "naturalized_text": BLOB,
                "final_text": BLOB,
                "tts_text": BLOB,
                "semantic_text": BLOB,
                "semantic_engine_text": BLOB,
            },
        ],
        "segments_data": [
            {"index": 0, "text": BLOB, "final_text": BLOB},
            {"index": 1, "text": BLOB, "final_text": BLOB},
        ],
    }
    align_info_for_translation_review(info)
    a0 = info["translation_audits"][0]
    a1 = info["translation_audits"][1]
    assert a0["final_text"] != a1["final_text"]
    # Semantic must track the debleeded Final, not the pre-split blob.
    assert a0["semantic_engine_text"] == a0["final_text"]
    assert a1["semantic_engine_text"] == a1["final_text"]
    assert "суперечку" in a0["final_text"].lower()
    assert "перехрестя" in a1["final_text"].lower() or "лікарн" in a1["final_text"].lower()


def test_repair_restores_owned_split_when_tts_holds_blob():
    sd = [
        {
            "index": 0,
            "final_text": USC_A,
            "translated_text": USC_A,
            "tts_text": USC_BLOB,
            "text": USC_BLOB,
            "semantic_engine_text": USC_BLOB,
            "raw_translation": USC_BLOB,
        },
        {
            "index": 1,
            "final_text": USC_B,
            "translated_text": USC_B,
            "tts_text": USC_BLOB,
            "text": USC_BLOB,
            "semantic_engine_text": USC_BLOB,
            "raw_translation": USC_BLOB,
        },
    ]
    report = repair_neighbor_bleed(sd)
    assert report["healed"] >= 1
    assert sd[0]["tts_text"] == USC_A
    assert "заявку" in sd[0]["tts_text"]
    assert "Насправді" not in sd[1]["tts_text"] or sd[1]["tts_text"] == USC_B
    assert sd[1]["final_text"] == USC_B or "впевнений" in sd[1]["tts_text"]
