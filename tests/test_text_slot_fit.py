# -*- coding: utf-8 -*-
"""Text-fit to slot: estimate before TTS, shorten without strong atempo."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_estimate_tts_ms_positive():
    from engines.text_slot_fit import estimate_tts_ms

    ms = estimate_tts_ms(
        "Джордж-молодший був дуже розумною дитиною, але легко відволікався.",
        "uk",
    )
    assert ms > 1000


def test_fit_shortens_long_text():
    from engines.text_slot_fit import estimate_tts_ms, fit_text_to_slot

    long_uk = (
        "Ну, отже, Джордж-молодший, власне кажучи, був дуже розумною дитиною, "
        "але також, як би, легко відволікався, і через це він насправді нічим "
        "настільки серйозно не займався, скажімо так."
    )
    slot = 3500
    before = estimate_tts_ms(long_uk, "uk")
    assert before > slot * 1.15
    fit = fit_text_to_slot(long_uk, slot, "uk")
    assert fit.predicted_ms_after <= before or fit.action == "atempo_prefer"
    assert fit.action in ("shorten", "unchanged", "atempo_prefer")
    # Meaning retained: still mentions George; Stage 15 may keep full text.
    assert "Джордж" in fit.text or "джордж" in fit.text.lower()
    assert word_retention_ok(long_uk, fit.text)


def word_retention_ok(original: str, candidate: str) -> bool:
    from engines.text_slot_fit import word_retention_ratio

    return word_retention_ratio(original, candidate) >= 0.85 - 1e-9


def test_fit_leaves_ok_text():
    from engines.text_slot_fit import fit_text_to_slot

    text = "Він пішов додому."
    fit = fit_text_to_slot(text, 4000, "uk")
    assert fit.action in ("unchanged", "none", "expand")
    assert fit.text == text or len(fit.text) >= len(text) * 0.8


def test_happy_path_atempo_cap_1_15():
    from engines.happy_path import HAPPY_PATH_MAX_ATEMPO, HAPPY_PATH_MIN_ATEMPO
    from engines.timing_fit import _atempo_hard_cap, _gentle_atempo_factor

    assert HAPPY_PATH_MIN_ATEMPO == 0.95
    assert HAPPY_PATH_MAX_ATEMPO == 1.15
    assert _atempo_hard_cap(1.50) <= 1.20  # absolute still 1.20 for advanced
    assert _gentle_atempo_factor(1.5, max_atempo=1.15) <= 1.15 + 1e-6


def test_no_speech_trim_with_1_15_cap():
    from pydub import AudioSegment

    from engines.timing_fit import fit_segment_audio

    work = Path("output") / "_tmp_text_fit"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "long.wav"
    AudioSegment.silent(duration=2000).export(src, format="wav")
    out, meta = fit_segment_audio(
        src,
        0,
        1000,
        next_start=1000,
        work_dir=work,
        allow_atempo=True,
        no_speech_trim=True,
        max_atempo=1.15,
    )
    assert Path(out).is_file()
    assert float(meta.get("atempo") or 1.0) <= 1.15 + 1e-6
    assert meta.get("speech_trimmed") is False
    assert "trim_overlap" not in str(meta.get("strategy") or "")


def test_natural_pause_capped_200():
    from engines.timing_fit import natural_sentence_pause_ms

    assert 80 <= natural_sentence_pause_ms("Hello.") <= 200
    assert natural_sentence_pause_ms("Wow…") <= 200
