# -*- coding: utf-8 -*-
"""Hard audio trim must sync Review Final/TTS to the spoken prefix."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.tts_audio_text_sync import (
    apply_audio_trim_text_sync,
    estimate_spoken_prefix,
    prefer_spoken_over_longer_final,
)
from engines.translation_review import build_translation_review


def test_estimate_spoken_prefix_cuts_tail():
    text = (
        "Тож за два тижні до того, коли Джордж повертав, а потім щось трапилося, "
        "ну це було так: по дорозі мчала інша машина й так сильно врізалася в "
        "машину Джорджа, що Джорджа-молодшого викинуло з машини, але він вижив."
    )
    # ~2.5s slot vs ~5.2s TTS → roughly half the words
    out = estimate_spoken_prefix(text, tts_ms=5200, spoken_ms=2500)
    assert out
    assert len(out) < len(text)
    assert text.startswith(out.rstrip(".,;:!? ")[:20]) or out in text


def test_apply_audio_trim_text_sync_updates_final_and_tts():
    full = (
        "Джордж-молодший підійшов до подіуму, щоб сфотографувати водія-переможця, "
        "але коли він підійшов до нього, цей чоловік середнього віку підійшов до "
        "нього та просто запитав Джорджа-молодшого про його фотографію."
    )
    seg = {
        "index": 0,
        "final_text": full,
        "tts_text": full,
        "text": full,
        "plain_text": full,
        "tts_ms": 32000,
    }
    placements = [
        {
            "idx": 0,
            "strategy": "trim_overlap+trim_overlap_word_boundary",
            "tts_ms": 32000,
            "speech_ms": 4100,
            "speech_trimmed": True,
            "fitted_ms": 4200,
            "pause_added_ms": 100,
        }
    ]
    n = apply_audio_trim_text_sync([seg], placements, placed_seg_indices=[0])
    assert n == 1
    assert seg["voice_truncated"] is True
    assert len(seg["final_text"]) < len(full)
    assert seg["final_text"] == seg["tts_text"] == seg["text_for_tts"]
    assert seg.get("text_before_audio_fit") == full


def test_review_prefers_spoken_when_voice_truncated():
    full = "Ааа ббб ввв ггг ддд еее жжж ззз иии ккк."
    spoken = "Ааа ббб ввв ггг."
    assert (
        prefer_spoken_over_longer_final(
            final=full,
            spoken=spoken,
            seg={"voice_truncated": True},
            audit={},
        )
        == spoken
    )


def test_build_translation_review_shows_spoken_not_uncut_blob():
    full = (
        "Тож за два тижні до того, коли Джордж повертав, а потім щось трапилося, "
        "ну це було так: по дорозі мчала інша машина й так сильно врізалася."
    )
    spoken = "Тож за два тижні до того, коли Джордж повертав,"
    info = {
        "source_segments": ["So two weeks earlier when George was making that turn"],
        "target_lang": "uk",
        "tts_files": ["x.wav"],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": full,
                "naturalized_text": full,
                "final_text": spoken,
                "tts_text": spoken,
                "voice_truncated": True,
            }
        ],
        "segments_data": [
            {
                "index": 0,
                "text": spoken,
                "final_text": spoken,
                "tts_text": spoken,
                "plain_text": spoken,
                "voice_truncated": True,
                "slot_ms": 2500,
                "tts_ms": 5200,
                "overflow_ms": 2700,
                "timing_meta": {
                    "speech_trimmed": True,
                    "spoken_fit_text": spoken,
                    "strategy": "trim_overlap",
                },
            }
        ],
    }
    review = build_translation_review(info)
    seg = review["segments"][0]
    assert seg["final_text"] == spoken
    assert seg["text_for_tts"] == spoken
    assert "врізалася" not in seg["final_text"]
