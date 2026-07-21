# -*- coding: utf-8 -*-
"""Original underlay volume: settings + review approve wiring."""

from __future__ import annotations

from engines.dub_style_presets import resolve_dub_style


def test_resolve_custom_mix_when_original_gt_zero():
    r0 = resolve_dub_style("modern", original_volume=0.0)
    assert r0["mix_mode"] == "full_dub"
    assert r0["mix_volumes"]["original_volume"] == 0.0

    r20 = resolve_dub_style("modern", original_volume=0.2)
    assert r20["mix_mode"] == "custom"
    assert abs(r20["mix_volumes"]["original_volume"] - 0.2) < 1e-6
    assert abs(r20["mix_volumes"]["background_volume"] - 0.2) < 1e-6


def test_approve_updates_mix_volumes_backup(monkeypatch):
    """Simulate approve patching mix_volumes_backup from original_volume pct."""
    info = {
        "dub_style": "modern",
        "mix_mode_backup": "full_dub",
        "mix_volumes_backup": {
            "original_volume": 0.0,
            "dub_volume": 1.0,
            "background_volume": 0.0,
        },
    }
    raw = 40  # percent
    pct = raw / 100.0
    resolved = resolve_dub_style(info.get("dub_style"), original_volume=pct)
    info["mix_volumes"] = dict(resolved["mix_volumes"])
    info["mix_volumes_backup"] = dict(resolved["mix_volumes"])
    info["mix_mode"] = resolved["mix_mode"]
    info["mix_mode_backup"] = resolved["mix_mode"]
    assert info["mix_mode_backup"] == "custom"
    assert abs(info["mix_volumes_backup"]["original_volume"] - 0.4) < 1e-6


def test_haskell_usc_overflow_shorten():
    from engines.meaning_fit.semantic_shorten import _rule_shorten

    long = (
        "А коли Хаскелл почув це, він сказав, Джордж, я знаю людей в Ю Ес Сі"
    )
    out = _rule_shorten(long)
    assert out is not None
    assert len(out) < len(long)
    assert "Хаскелл сказав" in out


def test_resolve_mix_honors_original_volume_under_full_dub():
    from engines.dub_engine import resolve_mix_volumes

    mode, orig, dub, _bg, _warn = resolve_mix_volumes(
        "full_dub",
        original_volume=0.2,
        dub_volume=1.0,
    )
    assert mode == "custom"
    assert orig > 0.05
    assert dub >= 0.85


def test_hospital_naturalize_short_not_ping_pong():
    from engines.translation_naturalizer import naturalize_uk

    out = naturalize_uk(
        "Через два тижні Джордж Джер. прокладався в стаціонарному комплексі в місцевій лікарні."
    )
    assert "реанімації" in out.lower()
    assert "інтенсивної терапії" not in out.lower()
