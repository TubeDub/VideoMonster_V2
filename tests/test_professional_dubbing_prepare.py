from __future__ import annotations

from engines.professional_dubbing.prepare import prepare_tts_groups_prosody


def test_prepare_tts_groups_uses_voice_hints(monkeypatch):
    captured: dict[str, str | None] = {}

    monkeypatch.setattr("engines.professional_dubbing.prepare.is_enabled", lambda: True)
    monkeypatch.setattr(
        "engines.professional_dubbing.prepare.is_prosody_style",
        lambda style_id, delivery: True,
    )

    class _Plan:
        plain_text = "Привіт."
        text_for_tts = "Привіт."
        suggested_rate = "-8%"
        suggested_pitch = "+6%"
        place_delay_ms = 0
        lead_in_ms = 0

        def to_dict(self):
            return {
                "plain_text": self.plain_text,
                "text_for_tts": self.text_for_tts,
                "suggested_rate": self.suggested_rate,
                "suggested_pitch": self.suggested_pitch,
                "place_delay_ms": 0,
                "lead_in_ms": 0,
                "pauses": [],
                "accents": [],
                "segment_ms": 3000,
                "est_ms_before": 2000,
                "est_ms_after": 2100,
                "fill_percent": 70.0,
                "underfill": False,
                "decisions": [],
                "source_cues": {},
            }

    def _fake_plan(text, *, segment_ms, lang, base_rate, base_pitch, use_ssml, source_cues, is_continuation):
        captured["base_rate"] = base_rate
        captured["base_pitch"] = base_pitch
        return _Plan()

    monkeypatch.setattr(
        "engines.professional_dubbing.prepare.build_prosody_plan",
        _fake_plan,
    )

    groups, meta = prepare_tts_groups_prosody(
        [{"indices": [0], "text": "Привіт.", "timing": [0, 3000]}],
        lang="uk",
        style_id="modern",
        delivery="cinematic",
        base_rate="+0%",
        base_pitch="+0%",
        segment_voice_hints={0: {"rate": "-8%", "pitch": "+6%", "intonation": "expressive"}},
        task_id="voice-test",
    )

    assert meta["enabled"] is True
    assert captured["base_rate"] == "-8%"
    assert captured["base_pitch"] == "+6%"
    assert groups[0]["prosody_rate"] == "-8%"
    assert groups[0]["prosody_pitch"] == "+6%"
    assert groups[0]["voice_direction"]["intonation"] == "expressive"
