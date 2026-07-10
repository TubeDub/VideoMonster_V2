"""Unit checks for segment alignment (split_by_timing_map recovery)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.cleaner import align_segments_to_timing_map, split_by_timing_map


def test_single_block_split():
    timing = [{"start": i * 1000, "end": (i + 1) * 1000} for i in range(5)]
    text = "One two three. Four five six. Seven eight."
    out = split_by_timing_map(text, timing)
    assert len(out) == 5, f"expected 5 got {len(out)}"
    assert all(isinstance(x, str) for x in out)


def test_align_pad():
    timing = [{"start": 0, "end": 1000}] * 3
    out = align_segments_to_timing_map(["a", "b"], timing)
    assert len(out) == 3


def test_align_redistribute_short_text():
    timing = [{"start": i * 1000, "end": (i + 1) * 1000} for i in range(2)]
    out = align_segments_to_timing_map(["hello world"], timing)
    assert len(out) == 2, f"expected 2 got {len(out)}"
    assert all(isinstance(x, str) and x for x in out), "both segments should have text"


def test_split_redistribute_enough_words():
    timing = [{"start": i * 1000, "end": (i + 1) * 1000} for i in range(5)]
    text = "alpha beta gamma delta epsilon"
    out = split_by_timing_map(text, timing)
    assert len(out) == 5, f"expected 5 got {len(out)}"
    assert all(s.strip() for s in out), "all segments should have text after redistribute"


def main() -> int:
    test_single_block_split()
    test_align_pad()
    test_align_redistribute_short_text()
    test_split_redistribute_enough_words()
    print("segment alignment OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
