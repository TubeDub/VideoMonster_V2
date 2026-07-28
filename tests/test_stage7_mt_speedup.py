# -*- coding: utf-8 -*-
"""Stage7 MT batch + disk cache unit tests."""

from __future__ import annotations

from pathlib import Path

from engines.mt_batch import translate_segments_batch
from engines.mt_cache import lookup_mt_cache, mt_cache_key, store_mt_cache


def test_mt_cache_roundtrip(tmp_path: Path):
    store_mt_cache(
        "Hello world",
        "Привіт світ",
        "en",
        "uk",
        engine="auto",
        cache_dir=tmp_path,
    )
    hit = lookup_mt_cache("Hello world", "en", "uk", engine="auto", cache_dir=tmp_path)
    assert hit == "Привіт світ"
    key = mt_cache_key("Hello   world", "en", "uk", engine="auto")
    assert key == mt_cache_key("Hello world", "en", "uk", engine="auto")


def test_translate_segments_batch_parity_and_cache(tmp_path: Path, monkeypatch):
    calls = {"n": 0}

    def fake_traced(text, src, tgt, **kwargs):
        calls["n"] += 1
        return f"TR:{text}", {"engine": "fake"}

    monkeypatch.setattr("engines.translation.translate_text_traced", fake_traced)
    monkeypatch.setattr(
        "engines.mt_batch._try_marian_batch", lambda *a, **k: None
    )

    segs = ["One", "Two", "Three"]
    out1, st1 = translate_segments_batch(
        segs,
        "en",
        "uk",
        batch_size=2,
        concurrency=1,
        cache_dir=tmp_path,
        prefer_marian=False,
    )
    assert len(out1) == 3
    assert out1 == ["TR:One", "TR:Two", "TR:Three"]
    assert st1["mt_cache_misses"] == 3
    assert st1["mt_calls"] == 3
    assert calls["n"] == 3

    out2, st2 = translate_segments_batch(
        segs,
        "en",
        "uk",
        batch_size=2,
        concurrency=1,
        cache_dir=tmp_path,
        prefer_marian=False,
    )
    assert out2 == out1
    assert st2["mt_cache_hits"] == 3
    assert st2["mt_cache_misses"] == 0
    assert st2["mt_calls"] == 0
    assert calls["n"] == 3  # no new engine calls
