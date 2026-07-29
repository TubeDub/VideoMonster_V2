# -*- coding: utf-8 -*-
"""Stage 11 — MT cache bypass: v3 key, stricter incomplete, glossary-on-hit."""

from __future__ import annotations

from pathlib import Path

from engines.mt.glossary_en_uk import finalize_mt_text
from engines.mt_batch import translate_segments_batch
from engines.mt_cache import (
    is_incomplete_mt_pair,
    lookup_mt_cache,
    mt_cache_key,
    store_mt_cache,
)
from engines.text_slot_fit import _safe_shorten


def test_cache_key_contains_v3():
    key = mt_cache_key("Hello", "en", "uk", engine="auto")
    # Key is sha1 hex — verify payload via different suffix producing different hash
    from engines import mt_cache as mc

    assert "v3_glossary_split" in mc._CACHE_KEY_SUFFIX
    # v2 vs v3 must diverge
    import hashlib

    v2 = hashlib.sha1(
        "Hello|en|uk|auto|v2_osplit".encode("utf-8")
    ).hexdigest()
    assert key != v2
    assert len(key) == 40


def test_incomplete_ratio_half():
    # 50 words EN, 24 UK words → 0.48 < 0.50 → incomplete
    src = " ".join([f"word{i}" for i in range(50)])
    short = " ".join(["слово"] * 24)
    assert is_incomplete_mt_pair(src, short, "en", "uk")
    # 26/50 = 0.52 → ok for ratio (unless other rules)
    ok = " ".join(["слово"] * 26)
    assert not is_incomplete_mt_pair(src, ok, "en", "uk")


def test_incomplete_star_wars_missing():
    src = (
        "Years later George Lucas made Star Wars and changed cinema forever "
        "with a new kind of acceptance letter story about film school."
    )
    # Long enough + missing Star Wars / Lucas markers
    bad = "Пізніше він зняв фільм і змінив кіно назавжди з новою історією."
    assert is_incomplete_mt_pair(src, bad, "en", "uk")
    good = (
        "Пізніше Джордж Лукас зняв Зоряні війни і змінив кіно назавжди "
        "з новою історією про лист прийняття."
    )
    assert not is_incomplete_mt_pair(src, good, "en", "uk")


def test_glossary_on_cached_string():
    raw = "батько купив йому Файта."
    fixed = finalize_mt_text("en", "uk", raw)
    assert "Фіат" in fixed
    assert "Файта" not in fixed


def test_glossary_applied_on_cache_hit(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VM_MT_NO_CACHE", raising=False)
    src = "his father bought him a Fiat."
    # Store pre-glossary bad form under v3 key
    store_mt_cache(src, "батько купив йому Файта.", "en", "uk", cache_dir=tmp_path)

    def boom(*a, **k):
        raise AssertionError("Marian must not run on cache hit")

    monkeypatch.setattr("engines.mt_batch._try_marian_batch", boom)
    out, st = translate_segments_batch(
        [src],
        "en",
        "uk",
        cache_dir=tmp_path,
        prefer_marian=True,
        concurrency=1,
    )
    assert st["mt_cache_hits"] == 1
    assert "Фіат" in out[0]
    assert "Файта" not in out[0]
    assert st["mt_segment_engines"][0] == "cache+glossary"


def test_vm_mt_no_cache_forces_miss(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VM_MT_NO_CACHE", "1")
    src = "Hello world."
    store_mt_cache = __import__("engines.mt_cache", fromlist=["store_mt_cache"]).store_mt_cache
    # store is also disabled under NO_CACHE — plant file manually
    from engines.mt_cache import cache_path_for_key, mt_cache_key
    import json

    key = mt_cache_key(src, "en", "uk")
    path = cache_path_for_key(tmp_path, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"source": src, "translated": "Привіт світ", "engine": "auto"}),
        encoding="utf-8",
    )
    assert lookup_mt_cache(src, "en", "uk", cache_dir=tmp_path) is None


def test_shorten_refuses_star_wars_tail():
    text = (
        "Він навчався у кіношколі. Пізніше Джордж Лукас зняв Зоряні війни."
    )
    # Tiny slot forces shorten attempt
    out, reasons, _ = _safe_shorten(
        text, slot_ms=400, lang="uk", source_hint="George Lucas made Star Wars"
    )
    assert "Зоряні" in out or "Лукас" in out
    assert "shorten_refused_critical_tail" in reasons or out == text or (
        "Зоряні" in out and "Лукас" in out
    )
