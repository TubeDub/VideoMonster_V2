# -*- coding: utf-8 -*-
"""Stage 10 — MT Completeness Lock (Simple): split, beams, cache reject, glossary."""

from __future__ import annotations

from pathlib import Path

from engines.mt.glossary_en_uk import (
    apply_glossary_en_uk,
    protect_glossary,
)
from engines.mt.oversized_guard import (
    guard_segments_before_mt,
    is_oversized_mt_unit,
    split_oversized_unit,
)
from engines.mt.stable_translate import resolve_marian_beams
from engines.mt_batch import translate_segments_batch
from engines.mt_cache import (
    is_incomplete_mt_pair,
    lookup_mt_cache,
    store_mt_cache,
)

MEGA_EN = (
    "But, as he was driving, George Jr. could not help but feel like he was "
    "really dreading actually getting there. So George Jr. was a very smart kid, "
    "but he also got distracted really easily and because of that, he really had "
    "not pursued anything all that seriously that is except for cars. And at that "
    "point his father actually bought him a small Italian car called the Fiat."
)


def test_oversized_guard_splits_mega():
    assert is_oversized_mt_unit(MEGA_EN)
    parts = split_oversized_unit(MEGA_EN)
    assert len(parts) >= 2
    assert all(not is_oversized_mt_unit(p) or len(p.split()) <= 55 for p in parts)


def test_guard_segments_parent_rejoin_parity():
    segs = ["Short one.", MEGA_EN, "Another short."]
    g = guard_segments_before_mt(segs, log=False)
    assert len(g.texts) == len(g.parent_indices)
    assert g.split_count >= 1
    buckets: list[list[str]] = [[] for _ in segs]
    for ui, parent in enumerate(g.parent_indices):
        buckets[parent].append(g.texts[ui])
    rejoined = [" ".join(b).strip() for b in buckets]
    assert len(rejoined) == 3
    assert "Fiat" in rejoined[1]
    assert rejoined[0] == "Short one."


def test_resolve_marian_beams_default_two(monkeypatch):
    monkeypatch.delenv("MT_NUM_BEAMS", raising=False)
    monkeypatch.delenv("VM_MT_NUM_BEAMS", raising=False)
    assert resolve_marian_beams(simple=True) == 2


def test_resolve_marian_beams_env(monkeypatch):
    monkeypatch.setenv("MT_NUM_BEAMS", "3")
    assert resolve_marian_beams(simple=True) == 3
    monkeypatch.setenv("MT_NUM_BEAMS", "9")
    assert resolve_marian_beams(simple=True) == 4


def test_cache_rejects_short_oversized_translation(tmp_path: Path):
    short_uk = "Джордж їхав."
    assert is_incomplete_mt_pair(MEGA_EN, short_uk, "en", "uk")
    path = store_mt_cache(
        MEGA_EN, short_uk, "en", "uk", engine="auto", cache_dir=tmp_path
    )
    assert path is None
    assert lookup_mt_cache(MEGA_EN, "en", "uk", engine="auto", cache_dir=tmp_path) is None


def test_cache_accepts_full_joined_translation(tmp_path: Path):
    full = (
        "Але коли він їхав, Джордж-молодший відчував страх. "
        "Тож Джордж-молодший був дуже розумним хлопцем, але легко відволікався, "
        "і через це він насправді не займався нічим серйозним, окрім автомобілів. "
        "І в той момент батько купив йому невелику італійську машину під назвою Фіат."
    )
    assert not is_incomplete_mt_pair(MEGA_EN, full, "en", "uk")
    path = store_mt_cache(MEGA_EN, full, "en", "uk", engine="auto", cache_dir=tmp_path)
    assert path is not None
    hit = lookup_mt_cache(MEGA_EN, "en", "uk", engine="auto", cache_dir=tmp_path)
    assert hit == full


def test_glossary_protect_restore_fiat():
    """Stage 14b: protect is no-op; Fiat fixed post-MT."""
    src = "his father bought him a Fiat."
    protected, forms = protect_glossary(src)
    assert protected == src
    assert forms == []
    fixed = apply_glossary_en_uk(src)
    assert "Фіат" in fixed
    assert "Fiat" not in fixed
    fixed2 = apply_glossary_en_uk("купив Файта.")
    assert "Фіат" in fixed2
    assert "Файта" not in fixed2


def test_glossary_star_wars_usc():
    out = apply_glossary_en_uk("He loved Star Wars and people at USC.")
    assert "Зоряні війни" in out
    assert "USC" in out


def test_mt_batch_guard_then_marian_rejoin(tmp_path: Path, monkeypatch):
    """Miss path: guard expand → fake Marian on units → rejoin 1:1."""

    def fake_marian(texts, src, tgt, *, app_dir):
        # Return long-enough UK so cache short-reject (0.50 ratio) does not fire.
        return [
            (" ".join(["слово"] * max(12, len(t.split()))), {"engine": "marian"})
            for t in texts
        ]

    monkeypatch.setattr("engines.mt_batch._try_marian_batch", fake_marian)
    segs = ["Hello.", MEGA_EN]
    out, st = translate_segments_batch(
        segs,
        "en",
        "uk",
        batch_size=10,
        concurrency=1,
        cache_dir=tmp_path,
        prefer_marian=True,
    )
    assert len(out) == 2
    assert out[0]
    assert len(out[1].split()) >= 20
    assert st.get("mt_guard_splits", 0) >= 1
    hit = lookup_mt_cache(MEGA_EN, "en", "uk", engine="auto", cache_dir=tmp_path)
    assert hit == out[1]
