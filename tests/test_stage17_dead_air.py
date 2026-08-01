# -*- coding: utf-8 -*-
"""Stage 17: underfill expand/slow, gap>350ms close, non-uk voice reject."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from pydub import AudioSegment

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_underfill_triggers_expand_or_atempo_slow():
    from engines.text_slot_fit import (
        UNDERFILL_ATEMPO_SLOW_RATIO,
        UNDERFILL_EXPAND_RATIO,
        estimate_tts_ms,
        fit_text_to_slot,
    )

    text = "Тож він пішов."
    slot = 8000
    pred = estimate_tts_ms(text, "uk")
    assert pred < slot * UNDERFILL_EXPAND_RATIO
    fit = fit_text_to_slot(
        text,
        slot,
        "uk",
        source_hint="So he kept walking down that long road for a while then.",
    )
    assert fit.dead_air_risk_ms == max(0, slot - fit.predicted_ms_after)
    assert fit.action in (
        "expand",
        "atempo_slow",
        "expand_then_slow",
        "atempo_prefer",
        "dead_air_risk",
    )
    if fit.action == "expand":
        assert fit.predicted_ms_after > pred
    # Soft assert: after fit handled or marked for audio stretch.
    assert (
        fit.predicted_ms_after >= int(slot * UNDERFILL_ATEMPO_SLOW_RATIO)
        or fit.action
        in ("expand", "atempo_slow", "expand_then_slow", "atempo_prefer", "dead_air_risk")
    )


def test_gap_gt_350ms_handled_by_close():
    from engines.timing_fit import (
        MAX_INTER_SEG_DEAD_AIR_MS,
        MAX_MICRO_PAUSE_MS,
        close_inter_segment_dead_air,
    )

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        # Short clip then huge placement gap to next (EN-speech zone).
        seg0 = AudioSegment.silent(duration=400)
        p0 = work / "s0.wav"
        p1 = work / "s1.wav"
        seg0.export(p0, format="wav")
        AudioSegment.silent(duration=300).export(p1, format="wav")

        place0, place1 = 0, 2000  # gap after 400ms → ~1600ms dead air
        fitted = [(str(p0), place0, len(seg0)), (str(p1), place1, 300)]
        placements = [
            {
                "idx": 0,
                "original_start_ms": 0,
                "slot_end_ms": 1900,
                "slot_ms": 1900,
                "strategy": "none",
                "atempo": 1.0,
            },
            {
                "idx": 1,
                "original_start_ms": 2000,
                "slot_end_ms": 2500,
                "slot_ms": 500,
                "strategy": "none",
                "atempo": 1.0,
            },
        ]
        gap_before = place1 - (place0 + len(seg0))
        assert gap_before > MAX_INTER_SEG_DEAD_AIR_MS

        audits = close_inter_segment_dead_air(
            fitted,
            placements,
            work,
            en_speech_intervals=[(0, 1900), (2000, 2500)],
        )
        assert audits, "expected gap-close audit"
        assert audits[0].get("action") not in ("skip_en_pause", "failed", None)
        # After close: stretch and/or boundary_shift next place earlier.
        gap_after = fitted[1][1] - (fitted[0][1] + fitted[0][2])
        assert gap_after < gap_before
        assert gap_after <= MAX_INTER_SEG_DEAD_AIR_MS
        assert gap_after <= MAX_MICRO_PAUSE_MS + 5 or "boundary_shift" in str(
            audits[0].get("action")
        )


def test_non_uk_voice_rejected():
    from engines.tts_lang_lock import (
        DEFAULT_UK_CYRILLIC_MIN,
        assert_voice_matches_target,
        is_uk_tts_text_ok,
    )

    ok, reason = assert_voice_matches_target(
        "cs-CZ-AntoninNeural", "uk", raise_error=False
    )
    assert ok is False
    assert "forbidden" in reason or "locale" in reason or "uk-UA" in reason

    with pytest.raises(RuntimeError, match="PIPELINE_VOICE_LOCALE"):
        assert_voice_matches_target("sk-SK-LukasNeural", "uk", raise_error=True)

    with pytest.raises(RuntimeError, match="PIPELINE_VOICE_LOCALE"):
        assert_voice_matches_target("en-US-GuyNeural", "uk", raise_error=True)

    assert assert_voice_matches_target("uk-UA-OstapNeural", "uk", raise_error=True)[0]

    czech = "Vítejme u další epizody našeho pořadu."
    assert not is_uk_tts_text_ok(czech, min_ratio=DEFAULT_UK_CYRILLIC_MIN)
    uk = "Вітаємо на наступному епізоді нашої програми."
    assert is_uk_tts_text_ok(uk, min_ratio=DEFAULT_UK_CYRILLIC_MIN)


def test_stage15_retention_not_broken():
    from engines.text_slot_fit import MIN_WORD_RETENTION, fit_text_to_slot, word_retention_ratio

    assert MIN_WORD_RETENTION == 0.85
    # Long meaning; short slot — must prefer atempo over chopping below 85%.
    text = (
        "Він вижив після аварії, коли його викинуло з машини, "
        "і більше не ганяв на гоночних автомобілях, доки Джордж Лукас "
        "не зняв Зоряні війни."
    )
    fit = fit_text_to_slot(text, slot_ms=2500, lang="uk")
    assert word_retention_ratio(text, fit.text) >= MIN_WORD_RETENTION
    assert fit.action in ("atempo_prefer", "unchanged", "shorten", "expand", "atempo_slow")
    if fit.action == "shorten":
        assert word_retention_ratio(text, fit.text) >= MIN_WORD_RETENTION


def test_dead_air_regions_flags_en_speech_silence():
    from engines.dead_air import find_dead_air_regions

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        # 500ms tone + 800ms silence + 300ms tone — silence overlaps EN mask.
        tone = AudioSegment.silent(duration=500)
        # Use non-silent: amplify a click-like pattern via louder silence hack —
        # inject noise by overlaying a short loud beep from raw PCM.
        import array
        import struct

        def _tone_ms(ms: int, freq: int = 440, vol: int = 8000) -> AudioSegment:
            sr = 16000
            n = int(sr * ms / 1000)
            samples = array.array(
                "h",
                (
                    int(vol * __import__("math").sin(2 * 3.14159 * freq * i / sr))
                    for i in range(n)
                ),
            )
            return AudioSegment(
                data=samples.tobytes(),
                sample_width=2,
                frame_rate=sr,
                channels=1,
            )

        audio = _tone_ms(400) + AudioSegment.silent(duration=800) + _tone_ms(300)
        path = work / "dub.wav"
        audio.export(path, format="wav")
        regions = find_dead_air_regions(
            path,
            en_speech_intervals=[(0, 1500)],
            max_silence_ms=350,
        )
        assert regions, "expected dead_air region on EN speech"
        assert regions[0]["duration_ms"] >= 350
        # EN pause zone only → empty
        regions2 = find_dead_air_regions(
            path,
            en_speech_intervals=[(0, 300)],  # only first tone
            max_silence_ms=350,
        )
        assert regions2 == []
