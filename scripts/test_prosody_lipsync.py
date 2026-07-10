#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.professional_dubbing.prosody import build_prosody_plan
from engines.professional_dubbing.source_cues import gap_to_break_ms
from engines.timing_fit import compress_internal_pauses
from pydub import AudioSegment


def test_gap_to_break_small():
    assert gap_to_break_ms(80) is None
    assert gap_to_break_ms(200) is not None


def test_uk_prosody_commas_not_too_short():
    text = (
        "просто не зрозумів одержимість свого сина автомобілями, наприклад, "
        "чому ти не можеш зосередитися і застосувати це до інших речей, "
        "отримаєш справжню роботу. І так по суті, кожна вечеря в ці дні, "
        "перетворювалася на велику суперечку між батьком і сином."
    )
    cues = {
        "internal_gaps_ms": [80, 83, 89, 82],
        "place_delay_ms": 120,
        "lead_break_ms": 300,
    }
    plan = build_prosody_plan(text, segment_ms=12720, lang="uk", source_cues=cues)
    comma_pauses = [p["ms"] for p in plan.pauses if p.get("after") == ","]
    assert all(ms >= 180 for ms in comma_pauses), comma_pauses
    assert plan.place_delay_ms <= 280
    assert plan.lead_in_ms == 0, "lead_in should be 0 when place_delay set"
    assert "break time=\"120ms\"" not in plan.text_for_tts


def test_pause_compress_preserves_sentence():
    audio = (
        AudioSegment.silent(duration=400, frame_rate=44100)
        + AudioSegment.silent(duration=350, frame_rate=44100)
        + AudioSegment.silent(duration=400, frame_rate=44100)
    )
    out, saved = compress_internal_pauses(audio, max_pause_ms=185)
    assert saved >= 0
    assert 1030 <= len(out) <= 1200


if __name__ == "__main__":
    test_gap_to_break_small()
    test_uk_prosody_commas_not_too_short()
    test_pause_compress_preserves_sentence()
    print("OK prosody lip-sync fixes")
