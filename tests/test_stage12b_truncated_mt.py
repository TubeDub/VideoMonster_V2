# -*- coding: utf-8 -*-
"""Stage 12b — truncated MT: ratio 0.55, words>55/tgt<40, _skip_cache_long."""

from __future__ import annotations

from pathlib import Path

from engines.mt_batch import _skip_cache_long, translate_segments_batch
from engines.mt_cache import is_incomplete_mt_pair, lookup_mt_cache, store_mt_cache


def test_short_ratio_is_055():
    from engines import mt_cache as mc

    assert mc._SHORT_RATIO == 0.55


def test_incomplete_long_src_ratio():
    src = " ".join([f"word{i}" for i in range(40)])
    assert is_incomplete_mt_pair(src, " ".join(["с"] * 21), "en", "uk")
    assert not is_incomplete_mt_pair(src, " ".join(["слово"] * 23), "en", "uk")


def test_incomplete_words_gt55_tgt_lt40():
    src = " ".join([f"word{i}" for i in range(60)])
    short = " ".join(["слово"] * 39)  # 39 < 40, ratio 39/60=0.65 > 0.55 but rule b
    assert is_incomplete_mt_pair(src, short, "en", "uk")
    ok = " ".join(["слово"] * 40)
    assert not is_incomplete_mt_pair(src, ok, "en", "uk")


def test_skip_cache_long_default_on(monkeypatch):
    monkeypatch.delenv("VM_MT_SKIP_CACHE_LONG", raising=False)
    long = " ".join([f"w{i}" for i in range(60)])
    assert _skip_cache_long(long) is True
    monkeypatch.setenv("VM_MT_SKIP_CACHE_LONG", "0")
    assert _skip_cache_long(long) is False


def test_long_never_serves_cache(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VM_MT_NO_CACHE", raising=False)
    monkeypatch.setenv("VM_MT_SKIP_CACHE_LONG", "1")
    long = " ".join([f"word{i}" for i in range(60)])
    full = " ".join(["слово"] * 60)
    store_mt_cache(long, full, "en", "uk", cache_dir=tmp_path)
    assert lookup_mt_cache(long, "en", "uk", cache_dir=tmp_path) == full

    calls = {"n": 0}

    def fake_marian(texts, src, tgt, *, app_dir):
        calls["n"] += 1
        return [
            (" ".join(["слово"] * max(12, len(t.split()))), {"engine": "marian"})
            for t in texts
        ]

    monkeypatch.setattr("engines.mt_batch._try_marian_batch", fake_marian)
    _out, st = translate_segments_batch(
        [long], "en", "uk", cache_dir=tmp_path, prefer_marian=True, concurrency=1
    )
    assert st["mt_long_cache_skips"] >= 1
    assert calls["n"] >= 1
    assert st["mt_segment_engines"][0] == "marian_batch"
