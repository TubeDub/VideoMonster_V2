"""Tests for translate lab (pipeline wiring)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_prepare_segments_paragraphs():
    from engines.translate_lab import prepare_source_segments

    text = "First paragraph.\n\nSecond paragraph."
    segs, tm, _ = prepare_source_segments(text)
    assert len(segs) == 2
    assert tm == []


def test_prepare_segments_srt_clean():
    from engines.translate_lab import prepare_source_segments

    raw = """1
00:00:01,000 --> 00:00:04,000
Hello world.

2
00:00:05,000 --> 00:00:08,000
Second line.
"""
    segs, tm, cleaned = prepare_source_segments(raw, clean=True)
    assert len(segs) >= 1
    assert cleaned


def main() -> int:
    test_prepare_segments_paragraphs()
    test_prepare_segments_srt_clean()
    print("translate lab tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
