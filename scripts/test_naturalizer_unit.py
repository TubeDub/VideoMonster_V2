"""Unit tests for translation naturalizer (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.translation_naturalizer import (
    build_tts_groups,
    dedupe_consecutive_similar,
    fix_phantom_cross_segment_repeats,
    merge_segments_for_tts,
    merge_segments_for_translation,
    naturalize_ru,
    naturalize_uk,
    polish_lines,
)


def test_naturalize_duplicate_subject():
    prev = "Коза ходила по полю"
    cur = "Коза жевала траву"
    out = naturalize_ru(cur, prev)
    assert out.lower().startswith("жевала") or "коза" not in out.lower().split()[0]


def test_dedupe_similar():
    lines = [
        "Коза ходила по полю",
        "Коза ходила по полю",
        "Она остановилась",
    ]
    out = dedupe_consecutive_similar(lines)
    assert len(out) == 2


def test_merge_translation_groups():
    segs = ["Hello", "world", "How are you"]
    timing = [
        {"start": 0, "end": 500},
        {"start": 600, "end": 1000},
        {"start": 5000, "end": 6000},
    ]
    groups = merge_segments_for_translation(segs, timing, max_gap_ms=500)
    assert groups[0] == [0, 1]
    assert groups[1] == [2]


def test_merge_tts_short_window():
    segs = ["Hi", "there", "Long sentence here."]
    timing = [
        {"start": 0, "end": 400},
        {"start": 500, "end": 900},
        {"start": 3000, "end": 5000},
    ]
    groups = merge_segments_for_tts(segs, timing, min_duration_ms=2000)
    assert groups[0] == [0, 1]
    assert groups[1] == [2]


def test_polish_lines_chain():
    raw = ["Коза ходила", "Коза жевала", "Пotom ushla".replace("P", "П").replace("o", "о")]
    out = polish_lines(raw, tgt_lang="ru", use_llm=False)
    assert len(out) == 3
    assert out[0].lower().startswith("коза")


def test_naturalize_uk_ruism():
    out = naturalize_uk("Він ще не знає, что робити")
    assert "ще" in out.lower()
    assert "що" in out.lower()
    assert "что" not in out.lower()


def test_polish_lines_uk():
    raw = ["Він ішов", "він він біг"]
    out = polish_lines(raw, tgt_lang="uk", use_llm=False)
    assert len(out) == 2
    assert out[1].lower().count("він") == 1


def test_build_tts_groups_span():
    segs = ["Hi", "there", "Long sentence here."]
    timing = [
        {"start": 0, "end": 400},
        {"start": 500, "end": 900},
        {"start": 3000, "end": 5000},
    ]
    groups = build_tts_groups(segs, timing, min_duration_ms=2000)
    assert len(groups) == 2
    assert groups[0]["indices"] == [0, 1]
    assert groups[0]["timing"] == [0, 900]
    assert "Hi there" in groups[0]["text"]


def test_phantom_repeat_strip():
    speech = ["Hello", "How are you", "Fine thanks", "See you"] * 5
    translated = ["Джордж младший"] * 20
    out = fix_phantom_cross_segment_repeats(speech, translated)
    assert sum(1 for x in out if x.strip()) <= 1


def main() -> int:
    test_naturalize_duplicate_subject()
    test_dedupe_similar()
    test_merge_translation_groups()
    test_merge_tts_short_window()
    test_build_tts_groups_span()
    test_phantom_repeat_strip()
    test_polish_lines_chain()
    test_naturalize_uk_ruism()
    test_polish_lines_uk()
    print("naturalizer unit tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
