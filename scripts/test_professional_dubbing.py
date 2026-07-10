"""Tests for Professional Dubbing prosody module."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_disabled_passthrough():
    os.environ["VM_PROFESSIONAL_DUBBING"] = "0"
    from engines.professional_dubbing.prepare import prepare_tts_groups_prosody

    groups = [{"text": "Hello world.", "indices": [0], "timing": [0, 5000]}]
    out, meta = prepare_tts_groups_prosody(groups, lang="en", style_id="professional")
    assert meta.get("skipped") is True
    assert out[0]["text"] == "Hello world."


def test_prosody_adds_ssml():
    os.environ["VM_PROFESSIONAL_DUBBING"] = "1"
    from engines.professional_dubbing.prepare import prepare_tts_groups_prosody

    text = "Перше речення. Друге, з паузами."
    groups = [{"text": text, "indices": [0], "timing": [0, 8000]}]
    out, meta = prepare_tts_groups_prosody(
        groups, lang="uk", style_id="professional", delivery="professional_studio"
    )
    assert meta.get("enabled") is True
    tts_text = out[0]["text"]
    assert "<speak" in tts_text
    assert "break time" in tts_text
    assert out[0].get("plain_text") == text


def test_underfill_suggests_slower_rate():
    os.environ["VM_PROFESSIONAL_DUBBING"] = "1"
    from engines.professional_dubbing.prosody import build_prosody_plan

    plan = build_prosody_plan(
        "Коротко.",
        segment_ms=6000,
        lang="uk",
        base_rate="-4%",
    )
    assert plan.underfill is True
    assert plan.suggested_rate is not None


if __name__ == "__main__":
    test_disabled_passthrough()
    test_prosody_adds_ssml()
    test_underfill_suggests_slower_rate()
    print("OK: test_professional_dubbing")
