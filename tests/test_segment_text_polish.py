"""Tests for segment text polish and fitted wav repair helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.segment_text_polish import polish_segments_for_tts


def test_polish_fixes_george_jr_and_mog():
    segments = [
        {
            "index": 0,
            "text": "Але коли Джордж ехав за кермом, він не мог відчувати.",
            "grammar_text": "Але коли Джордж ехав за кермом, він не мог відчувати.",
        }
    ]
    sources = ["But, as he was driving, George Jr. could not help but feel dread."]
    n = polish_segments_for_tts(segments, sources, target_lang="uk")
    text = segments[0]["text"]
    assert "не міг" in text
    assert "їхав" in text
    assert n >= 1


def test_polish_fixes_usc_garbage():
    segments = [
        {
            "index": 0,
            "text": (
                "Джордж молодший отримає листа від компанії з фільму \"Скарб США.\""
            ),
        }
    ]
    sources = [
        "George Jr. would receive an acceptance letter from USC's film school."
    ]
    polish_segments_for_tts(segments, sources, target_lang="uk")
    assert "Скарб США" not in segments[0]["text"]
    assert "USC" in segments[0]["text"]
