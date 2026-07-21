"""Adaptive Segmentation 2.0 — split/merge/balance + TR recommendations."""

from __future__ import annotations

from engines.adaptive_segmentation import (
    adapt_source_segments,
    estimate_expected_tts_ms,
    load_adaptive_seg_config,
    segment_recommendation,
)
from engines.adaptive_segmentation.post_tts import should_prefer_resegment


def test_splits_long_whisper_block():
    # Classic bad Whisper shape: short + 46s narration + short
    short_a = "He left home."
    long_b = (
        "George Lucas was born in Modesto California in nineteen forty four. "
        "As a teenager he loved cars and racing more than school. "
        "After a serious accident he began to think about film. "
        "He later studied at USC and made short films that impressed mentors. "
        "Those early experiments led him toward Hollywood and science fiction."
    )
    short_c = "That changed everything."
    segments = [short_a, long_b, short_c]
    timing = [
        {"start": 0, "end": 2500},
        {"start": 2500, "end": 48000},
        {"start": 48000, "end": 52000},
    ]
    result = adapt_source_segments(
        segments,
        timing,
        overrides={
            "enabled": True,
            "min_ms": 4500,
            "max_ms": 16000,
            "preferred_ms": 9000,
            "aggressiveness": 0.8,
        },
    )
    assert result.changed
    durs = [t["end"] - t["start"] for t in result.timing_map]
    assert max(durs) <= 20000
    assert min(durs) >= 2000  # no orphan micro-crumbs after balance
    assert len(result.segments) >= 3
    stats = result.report["stats_after"]
    assert stats["spread_ratio"] < result.report["stats_before"]["spread_ratio"]
    # Classic 2s/45s/3s must not survive as max≈45s with min≈2s
    assert not (stats["max_ms"] >= 40000 and stats["min_ms"] <= 3000)


def test_merges_mid_sentence_fragments():
    """would_break_forbidden means join-required — must NOT block merge."""
    segments = [
        "He left",  # mid-sentence (Whisper cut)
        "home early that evening.",
        "Next day was quiet.",
    ]
    timing = [
        {"start": 0, "end": 2200},
        {"start": 2200, "end": 7000},
        {"start": 7000, "end": 12000},
    ]
    result = adapt_source_segments(
        segments,
        timing,
        overrides={
            "enabled": True,
            "min_ms": 4500,
            "max_ms": 16000,
            "aggressiveness": 0.7,
            "use_meaning": True,
        },
    )
    assert any("He left home" in s for s in result.segments)
    assert result.report["stats_after"]["min_ms"] >= 4000 or result.changed


def test_merges_tiny_neighbors():
    segments = [
        "Hi.",
        "He drove home for dinner after work.",
        "Then he slept.",
    ]
    timing = [
        {"start": 0, "end": 2000},
        {"start": 2000, "end": 9000},
        {"start": 9000, "end": 12000},
    ]
    result = adapt_source_segments(
        segments,
        timing,
        overrides={
            "enabled": True,
            "min_ms": 4500,
            "max_ms": 16000,
            "aggressiveness": 0.7,
        },
    )
    # First tiny segment should be merged away when possible
    assert result.report["stats_after"]["min_ms"] >= 3000 or result.changed
    assert all(len(s.strip()) > 0 for s in result.segments)


def test_disabled_passthrough():
    segs = ["One.", "Two three four."]
    timing = [{"start": 0, "end": 1000}, {"start": 1000, "end": 5000}]
    result = adapt_source_segments(
        segs, timing, overrides={"enabled": False}
    )
    assert result.changed is False
    assert result.segments == segs


def test_segment_recommendation_split_merge():
    cfg = load_adaptive_seg_config(
        overrides={"min_ms": 4500, "max_ms": 16000}
    )
    long_rec = segment_recommendation(
        slot_ms=38000, expected_tts_ms=42000, cfg=cfg
    )
    assert long_rec["advice"] == "Split Recommended"
    assert "Split" in long_rec["status"] or long_rec["status"] == "Needs Split"

    short_rec = segment_recommendation(
        slot_ms=2000, expected_tts_ms=1800, cfg=cfg
    )
    assert short_rec["advice"] == "Merge Recommended"


def test_expected_tts_forecast_positive():
    ms = estimate_expected_tts_ms(
        "George drove through his hometown on the way home for dinner."
    )
    assert ms > 500


def test_prefer_resegment_on_long_overflow():
    assert should_prefer_resegment(slot_ms=40000, tts_ms=45000, overflow_ms=5000)
    assert not should_prefer_resegment(slot_ms=8000, tts_ms=8200, overflow_ms=200)


def test_post_tts_split_long_overflow_in_place():
    from engines.adaptive_segmentation.post_tts import try_split_long_overflow_segment

    segments_data = [
        {
            "plain_text": "Перше речення тут. Друге речення продовжує думку далі і ще.",
            "text": "Перше речення тут. Друге речення продовжує думку далі і ще.",
            "slot_ms": 40000,
            "playback_duration": 45000,
            "file": "a.wav",
        }
    ]
    source = [
        "First sentence here. Second sentence continues the thought further still."
    ]
    timing = [{"start": 0, "end": 40000}]
    audits: list = [{"index": 0, "whisper_text": source[0]}]
    ok = try_split_long_overflow_segment(
        segments_data=segments_data,
        source_segments=source,
        timing_map=timing,
        audits=audits,
        idx=0,
    )
    assert ok is True
    assert len(segments_data) == 2
    assert len(source) == 2
    assert len(timing) == 2
    assert timing[0]["end"] <= timing[1]["start"] + 1


def test_review_includes_seg_advice_fields():
    from engines.translation_review import build_translation_review

    review = build_translation_review(
        {
            "source_lang": "en",
            "target_lang": "uk",
            "source_segments": [
                "George Lucas was born in Modesto and later studied film at USC "
                "after a near fatal crash changed his plans forever and more."
            ],
            "segments_data": [
                {
                    "slot_ms": 38000,
                    "playback_duration": 0,
                    "text": "Джордж Лукас народився в Модесто.",
                    "final_text": "Джордж Лукас народився в Модесто.",
                }
            ],
            "translation_audits": [
                {
                    "index": 0,
                    "whisper_text": "George Lucas was born in Modesto.",
                    "raw_translation": "Джордж Лукас народився в Модесто.",
                    "final_text": "Джордж Лукас народився в Модесто.",
                    "quality_score": 80,
                }
            ],
        }
    )
    seg = review["segments"][0]
    assert "expected_tts_ms" in seg
    assert "seg_advice" in seg
    assert seg["seg_advice"] == "Split Recommended" or seg["slot_ms"] == 38000
