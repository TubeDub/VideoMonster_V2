# -*- coding: utf-8 -*-
"""Stage 18: hard-fail dead air + no skip→silence + uk voice lock."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_non_uk_voice_raises():
    from engines.tts_lang_lock import assert_voice_matches_target

    with pytest.raises(RuntimeError, match="PIPELINE_VOICE_LOCALE"):
        assert_voice_matches_target("cs-CZ-AntoninNeural", "uk", raise_error=True)
    with pytest.raises(RuntimeError, match="PIPELINE_VOICE_LOCALE"):
        assert_voice_matches_target("ru-RU-DmitryNeural", "uk", raise_error=True)
    with pytest.raises(RuntimeError, match="PIPELINE_VOICE_LOCALE"):
        assert_voice_matches_target("sk-SK-LukasNeural", "uk", raise_error=True)
    ok, _ = assert_voice_matches_target("uk-UA-OstapNeural", "uk", raise_error=True)
    assert ok


def test_empty_skip_text_path_raises_on_simple_uk(monkeypatch):
    from engines.tts_lang_lock import enforce_segments_lang_lock, guard_uk_tts_text

    with pytest.raises(RuntimeError, match="PIPELINE_LANG_MIX"):
        guard_uk_tts_text(
            "Vítejme u další epizody.",
            source_text="",
            allow_remt=False,
            fail_loud=True,
            segment_index=0,
        )

    # Empty Final on Simple → raise (no skip→silence).
    with pytest.raises(RuntimeError, match="PIPELINE_LANG_MIX"):
        enforce_segments_lang_lock(
            [{"final_tts_text": "", "original": "Hello there."}],
            target_lang="uk",
            source_lang="en",
            simple_mode=True,
            fail_loud=True,
            app_dir=ROOT,
        )

    # Bad cyrillic + remt still bad → raise (never skipped=True).
    monkeypatch.setattr(
        "engines.tts_lang_lock.force_remt_segment_no_cache",
        lambda *a, **k: "Ahoj světe toto není ukrajinsky text.",
    )
    segs = [
        {
            "final_tts_text": "Ahoj světe toto není ukrajinština vůbec.",
            "original": "Hello world this is not ukrainian at all really.",
        }
    ]
    with pytest.raises(RuntimeError, match="PIPELINE_LANG_MIX"):
        enforce_segments_lang_lock(
            segs,
            target_lang="uk",
            source_lang="en",
            simple_mode=True,
            fail_loud=True,
            app_dir=ROOT,
        )


def test_dead_air_regions_nonempty_fails_status(monkeypatch, tmp_path: Path):
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

    monkeypatch.setenv("VM_ALLOW_DEAD_AIR", "1")
    out = enforce_dead_air_or_fail(regions, simple_mode=True)
    assert out  # warning-only override


def test_retention_085_not_broken():
    from engines.text_slot_fit import MIN_WORD_RETENTION, fit_text_to_slot, word_retention_ratio

    assert MIN_WORD_RETENTION == 0.85
    text = (
        "Він вижив після аварії, коли його викинуло з машини, "
        "і більше не ганяв на гоночних автомобілях, доки Джордж Лукас "
        "не зняв Зоряні війни."
    )
    fit = fit_text_to_slot(text, slot_ms=2500, lang="uk")
    assert word_retention_ratio(text, fit.text) >= MIN_WORD_RETENTION


def test_tts_cache_key_includes_voice_and_lang():
    from engines.tts_cache import tts_cache_key

    a = tts_cache_key("Привіт", "uk-UA-OstapNeural", lang="uk")
    b = tts_cache_key("Привіт", "uk-UA-PolinaNeural", lang="uk")
    c = tts_cache_key("Привіт", "uk-UA-OstapNeural", lang="ru")
    assert a != b
    assert a != c


def test_final_only_resolve_prefers_final_not_naturalized():
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts

    seg = {
        "final_tts_text": "Фінальний текст українською мовою тут.",
        "naturalized_text": "Naturalized garbage should not be voiced.",
        "raw_translation": "Raw MT garbage should not be voiced.",
        "grammar_text": "Grammar buffer ignored when Final exists.",
    }
    assert resolve_segment_text_for_tts(seg) == "Фінальний текст українською мовою тут."
