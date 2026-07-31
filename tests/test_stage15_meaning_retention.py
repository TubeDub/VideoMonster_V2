# -*- coding: utf-8 -*-
"""Stage 15 — refuse Final/TTS truncation vs Raw MT; prefer atempo."""

from __future__ import annotations

from engines.text_slot_fit import (
    MIN_WORD_RETENTION,
    MIN_WORD_RETENTION_SEVERE,
    fit_text_to_slot,
    prefer_full_meaning_text,
    word_retention_ratio,
)
from engines.translation_quality import accept_naturalizer_change


def test_retention_constants():
    assert MIN_WORD_RETENTION == 0.85
    assert MIN_WORD_RETENTION_SEVERE == 0.70


def test_fit_refuses_chopping_seg1_tail():
    """#1-style: tiny slot must not cut the 'розумна дитина' sentence."""
    raw = (
        "Вісімнадцятирічний Джордж молодший поїхав по дорозі додому на вечерю, "
        "але коли їхав, Джордж молодший не міг не відчувати, що він справді "
        "боїться потрапити туди. Джордж молодший був дуже розумною дитиною, "
        "але він також дуже легко відволікся, і тому він не займався чимось "
        "настільки серйозним."
    )
    fit = fit_text_to_slot(raw, slot_ms=2500, lang="uk")
    assert word_retention_ratio(raw, fit.text) >= 0.85 - 1e-9
    assert "розумн" in fit.text.lower() or fit.action == "atempo_prefer"
    assert fit.meaning_preserved is True
    if fit.text != raw:
        assert not fit.text.rstrip().endswith("відчувати")
        assert not fit.text.rstrip().endswith("відчувати.")


def test_prefer_full_meaning_restores_raw():
    raw = (
        "Через два роки Джордж молодший стояв на фініші і підняв фотоапарат. "
        "Він вирішив, що не хоче більше їздити на гоночних автомобілях."
    )
    short = "Через два роки Джордж молодший стояв на фініші і підняв фотоапарат."
    out, restored = prefer_full_meaning_text(short, raw)
    assert restored is True
    assert "не хоче більше їздити" in out
    assert word_retention_ratio(raw, out) >= 0.99


def test_naturalizer_rollback_over_15pct():
    raw = (
        "Джордж молодший був розумною дитиною але легко відволікався "
        "і тому нічим серйозним не займався насправді."
    )
    cut = "Джордж молодший був розумною дитиною."
    kept = accept_naturalizer_change(raw, cut, original="George Jr. was smart.")
    assert kept == raw


def test_tts_resolve_restores_from_raw():
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts

    seg = {
        "final_tts_text": "Через два роки Джордж молодший стояв на фініші.",
        "raw_translation": (
            "Через два роки Джордж молодший стояв на фініші і підняв фотоапарат. "
            "Він вирішив що не хоче більше їздити на гоночних автомобілях."
        ),
        "original_text": "Two years later George Jr. stood at the finish line.",
        "target_lang": "uk",
    }
    out = resolve_segment_text_for_tts(seg)
    assert "фотоапарат" in out or "гоночн" in out or "їздити" in out
