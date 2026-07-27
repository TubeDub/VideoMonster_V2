# -*- coding: utf-8 -*-
"""zh→uk must not ship Argos flower waffle; prefer deep / non-collapse."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_argos_rejects_meta_waffle():
    from engines.mt.argos_engine import ArgosEngine
    from engines.mt.cross_script_guard import is_meta_waffle

    flower = (
        "Ми можемо самі зателефонувати одержувачу і узгодити зручний час "
        "і місце вручення квітів, а якщо необхідно, то збережемо сюрприз."
    )
    assert is_meta_waffle(flower)

    eng = ArgosEngine()
    # Monkeypatch translator to return flower
    import engines.mt.argos_engine as mod

    class _Fake:
        def translate(self, _s):
            return flower

    old = mod._MODEL_CACHE.get("en->uk")
    mod._MODEL_CACHE["en->uk"] = _Fake()
    try:
        r = eng.translate(
            "We're eight generations pure, and it's amazing that you're pregnant.",
            "en",
            "uk",
        )
        assert r.text == ""
        assert r.error == "meta_waffle"
    finally:
        if old is None:
            mod._MODEL_CACHE.pop("en->uk", None)
        else:
            mod._MODEL_CACHE["en->uk"] = old


def test_zh_uk_rankings_prefer_nllb():
    from engines.mt.registry import engines_for_pair, load_pair_rankings

    ranks = load_pair_rankings(ROOT)
    assert ranks.get("zh->uk", [""])[0] == "nllb"
    primary, fallback = engines_for_pair(ROOT, "zh", "uk")
    assert primary == "nllb"
    assert fallback == "marian"


def test_zh_uk_fallback_route_via_en():
    from engines.translation_router import candidate_routes, fallback_route_for_pair

    fb = fallback_route_for_pair(ROOT, "zh", "uk")
    assert fb is not None
    assert fb.chain == [("zh", "en"), ("en", "uk")]
    routes = candidate_routes("zh", "uk", ROOT)
    labels = [r.name for r in routes]
    assert "direct" in labels
    assert any(r.name == "via_en" or (len(r.chain) == 2 and r.chain[0][1] == "en") for r in routes)


def test_ordered_boosts_deep_for_zh(monkeypatch):
    from engines.mt import registry as reg

    # Ensure deep ranks ahead of argos for zh→uk among available
    ordered = reg.ordered_engines_for_pair(ROOT, "zh", "uk")
    ids = [e.id for e in ordered]
    if "deep" in ids and "argos" in ids:
        assert ids.index("deep") < ids.index("argos")
