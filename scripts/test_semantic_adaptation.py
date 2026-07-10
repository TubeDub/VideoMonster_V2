"""Unit tests for universal semantic adaptation."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.semantic_adaptation import (
    adapt_text_to_window,
    estimate_tts_duration_ms,
    validate_adaptation_quality,
)


def test_estimate_duration_positive():
    ms = estimate_tts_duration_ms("Hello world this is a test", "en")
    assert ms > 500


def test_validate_rejects_empty():
    ok, note = validate_adaptation_quality("Hello world", "", tgt_lang="en")
    assert not ok
    assert note == "empty_adapted"


def test_validate_accepts_unchanged():
    ok, _ = validate_adaptation_quality("Привет мир", "Привет мир", tgt_lang="ru")
    assert ok


def test_adapt_skips_when_fits():
    text = "Hi."
    adapted, rec = adapt_text_to_window(text, 5000, tgt_lang="en", index=0)
    assert adapted == text
    assert rec is None


def test_adapt_shortens_long_line():
    long_line = (
        "This is a very long sentence that would definitely not fit "
        "into a very short dubbing window at all."
    )
    adapted, rec = adapt_text_to_window(
        long_line,
        800,
        source_hint="Short original",
        tgt_lang="en",
        index=1,
    )
    assert len(adapted) <= len(long_line)
    if rec:
        assert rec.chars_after <= rec.chars_before


if __name__ == "__main__":
    test_estimate_duration_positive()
    test_validate_rejects_empty()
    test_validate_accepts_unchanged()
    test_adapt_skips_when_fits()
    test_adapt_shortens_long_line()
    print("semantic_adaptation tests OK")
