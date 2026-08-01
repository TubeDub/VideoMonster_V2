# -*- coding: utf-8 -*-
"""Stage 19: expand-first slot fill, forbid fast+gap, dead-air hard-fail."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_short_final_long_slot_expand_or_slow():
    from engines.text_slot_fit import (
        UNDERFILL_EXPAND_RATIO,
        estimate_tts_ms,
        fit_text_to_slot,
        forbid_fast_then_gap,
    )

    text = "Тож він пішов."
    slot = 8000
    assert estimate_tts_ms(text, "uk") < slot * UNDERFILL_EXPAND_RATIO
    fit = fit_text_to_slot(
        text,
        slot,
        "uk",
        source_hint="So he kept walking down that long road for a while then.",
    )
    assert fit.strategy in (
        "expand",
        "atempo_slow",
        "expand_then_slow",
        "dead_air_risk",
        "ok",
    )
    assert fit.action in (
        "expand",
        "atempo_slow",
        "expand_then_slow",
        "dead_air_risk",
        "unchanged",
        "atempo_prefer",
    )
    # Fill ≥0.90 after expand, or flagged risk / slow path.
    assert (
        fit.fill_ratio >= UNDERFILL_EXPAND_RATIO
        or fit.strategy in ("atempo_slow", "expand_then_slow", "dead_air_risk")
        or fit.action in ("atempo_slow", "expand_then_slow", "dead_air_risk")
    )
    assert not forbid_fast_then_gap(fit.atempo, fit.fill_ratio)
    assert 0.85 <= fit.atempo <= 1.15


def test_forbid_fast_then_gap_not_final():
    from engines.text_slot_fit import (
        FORBIDDEN_FAST_THEN_GAP,
        forbid_fast_then_gap,
        fit_text_to_slot,
        suggested_atempo_for_fill,
    )

    assert FORBIDDEN_FAST_THEN_GAP is True
    assert forbid_fast_then_gap(1.12, 0.70) is True
    assert forbid_fast_then_gap(1.0, 0.70) is False
    assert forbid_fast_then_gap(1.12, 0.95) is False

    # Underfill → suggested atempo must be ≤1.0 (slow), never fast+gap.
    tempo = suggested_atempo_for_fill(predicted_ms=3000, slot_ms=8000)
    assert tempo <= 1.0
    assert tempo >= 0.85
    assert not forbid_fast_then_gap(tempo, 3000 / 8000)

    fit = fit_text_to_slot("Коротко.", 9000, "uk", source_hint="He spoke briefly then.")
    assert not forbid_fast_then_gap(fit.atempo, fit.fill_ratio)
    assert fit.atempo <= 1.05 or fit.fill_ratio >= 0.90


def test_post_mux_silence_raises_dead_air(monkeypatch, tmp_path: Path):
    from engines.dead_air import (
        DeadAirError,
        enforce_dead_air_or_fail,
        find_dead_air_regions,
    )
    from pydub import AudioSegment
    import array
    import math

    monkeypatch.delenv("VM_ALLOW_DEAD_AIR", raising=False)

    def _tone_ms(ms: int, freq: int = 440, vol: int = 8000) -> AudioSegment:
        sr = 16000
        n = int(sr * ms / 1000)
        samples = array.array(
            "h",
            (int(vol * math.sin(2 * math.pi * freq * i / sr)) for i in range(n)),
        )
        return AudioSegment(
            data=samples.tobytes(), sample_width=2, frame_rate=sr, channels=1
        )

    audio = _tone_ms(400) + AudioSegment.silent(duration=800) + _tone_ms(300)
    path = tmp_path / "dub.wav"
    audio.export(path, format="wav")
    regions = find_dead_air_regions(path, [(0, 1500)], max_silence_ms=350)
    assert regions
    with pytest.raises(DeadAirError) as ei:
        enforce_dead_air_or_fail(regions, simple_mode=True)
    assert ei.value.error_code == "PIPELINE_DEAD_AIR"


def test_no_pad_fillers_on_expand():
    from engines.text_slot_fit import expand_to_fill, strip_slot_pad_fillers

    text = "Джордж пішов далі."
    out, reasons = expand_to_fill(
        text,
        target_ms=6000,
        lang="uk",
        source_hint="George went further down the road then.",
    )
    cleaned = strip_slot_pad_fillers(out)
    assert "ось як це було тоді" not in cleaned.lower()
    assert "саме так:" not in cleaned.lower()
    assert cleaned


def test_retention_085_regression():
    from engines.text_slot_fit import MIN_WORD_RETENTION, fit_text_to_slot, word_retention_ratio

    assert MIN_WORD_RETENTION == 0.85
    text = (
        "Він вижив після аварії, коли його викинуло з машини, "
        "і більше не ганяв на гоночних автомобілях, доки Джордж Лукас "
        "не зняв Зоряні війни."
    )
    fit = fit_text_to_slot(text, slot_ms=2500, lang="uk")
    assert word_retention_ratio(text, fit.text) >= MIN_WORD_RETENTION
