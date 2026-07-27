# -*- coding: utf-8 -*-
"""Stage 3 Happy Path: atempo ≤1.20, no speech hard-trim, overflow warn."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_happy_path_constants():
    from engines.happy_path import HAPPY_PATH_MAX_ATEMPO, HAPPY_PATH_NO_SPEECH_TRIM
    from engines.timing_fit import (
        DUB_MAX_ATEMPO,
        HAPPY_PATH_MAX_ATEMPO as TF_HP,
        _ATEMPO_ABSOLUTE_MAX,
        _ATEMPO_EMERGENCY_MAX,
    )

    assert HAPPY_PATH_MAX_ATEMPO == 1.20
    assert HAPPY_PATH_NO_SPEECH_TRIM is True
    assert TF_HP == 1.20
    assert _ATEMPO_ABSOLUTE_MAX == 1.20
    assert _ATEMPO_EMERGENCY_MAX == 1.20
    assert DUB_MAX_ATEMPO <= 1.20


def test_no_speech_trim_keeps_overflow_no_hard_cut():
    from pydub import AudioSegment

    from engines.timing_fit import fit_segment_audio

    work = Path("output") / "_tmp_hp_stage3"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "long_no_trim.wav"
    # 2000ms into 1000ms slot; next starts at 1000 — advanced would hard-cut.
    AudioSegment.silent(duration=2000).export(src, format="wav")
    out, meta = fit_segment_audio(
        src,
        0,
        1000,
        next_start=1000,
        work_dir=work,
        allow_atempo=False,
        no_speech_trim=True,
        max_atempo=1.20,
    )
    assert Path(out).is_file()
    fitted = AudioSegment.from_file(out)
    assert len(fitted) > 1000  # speech kept past hard_cap
    assert "trim_overlap" not in str(meta.get("strategy") or "")
    assert meta.get("speech_trimmed") is False
    assert meta.get("no_speech_trim") is True
    assert int(meta.get("overflow_ms") or 0) > 0
    assert "no_trim_overflow" in str(meta.get("strategy") or "")


def test_atempo_hard_cap_never_above_1_20():
    from engines.timing_fit import _atempo_hard_cap, _gentle_atempo_factor

    assert _atempo_hard_cap(2.5) <= 1.20
    assert _atempo_hard_cap(1.5) <= 1.20
    assert _gentle_atempo_factor(2.0, max_atempo=1.50) <= 1.20


def test_atempo_no_trim_path_caps_and_may_shorten():
    """With allow_atempo + no_speech_trim: atempo ≤1.20, never trim_overlap."""
    from pydub import AudioSegment

    from engines.timing_fit import fit_segment_audio

    work = Path("output") / "_tmp_hp_stage3"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "long_atempo.wav"
    AudioSegment.silent(duration=2000).export(src, format="wav")
    out, meta = fit_segment_audio(
        src,
        0,
        1000,
        next_start=1000,
        work_dir=work,
        allow_atempo=True,
        no_speech_trim=True,
        max_atempo=1.20,
    )
    assert Path(out).is_file()
    atempo = float(meta.get("atempo") or 1.0)
    assert atempo <= 1.20 + 1e-6
    assert "trim_overlap" not in str(meta.get("strategy") or "")
    assert meta.get("speech_trimmed") is False
    # Either fitted via atempo_no_trim or still overflow-kept
    strategy = str(meta.get("strategy") or "")
    assert "no_trim_overflow" in strategy or "atempo" in strategy


def test_advanced_path_still_trims_when_no_speech_trim_false():
    """Regression: advanced anti-bleed path must still hard-cut."""
    from pydub import AudioSegment

    from engines.timing_fit import fit_segment_audio

    work = Path("output") / "_tmp_hp_stage3"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "long_trim.wav"
    AudioSegment.silent(duration=2000).export(src, format="wav")
    out, meta = fit_segment_audio(
        src,
        0,
        1000,
        next_start=1000,
        work_dir=work,
        allow_atempo=False,
        no_speech_trim=False,
    )
    fitted = AudioSegment.from_file(out)
    assert len(fitted) <= 1000 + 20
    assert "trim_overlap" in str(meta.get("strategy") or "")
    assert meta.get("speech_trimmed") is True
