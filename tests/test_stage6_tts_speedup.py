# -*- coding: utf-8 -*-
"""Stage 6: TTS cache + parallel synthesize."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_tts_cache_key_stable(tmp_path):
    from engines.tts_cache import lookup_tts_cache, store_tts_cache, tts_cache_key

    k1 = tts_cache_key("Hello  world", "uk-UA-OstapNeural", rate="-5%", pitch="+0Hz")
    k2 = tts_cache_key("Hello world", "uk-UA-OstapNeural", rate="-5%", pitch="+0Hz")
    assert k1 == k2
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x" * 512)
    stored = store_tts_cache(
        src,
        "Hello world",
        "uk-UA-OstapNeural",
        rate="-5%",
        pitch="+0Hz",
        cache_dir=tmp_path / "cache",
    )
    assert stored is not None and stored.is_file()
    hit = lookup_tts_cache(
        "Hello world",
        "uk-UA-OstapNeural",
        rate="-5%",
        pitch="+0Hz",
        cache_dir=tmp_path / "cache",
    )
    assert hit is not None
    assert hit.stat().st_size >= 256


def test_empty_file_not_cache_hit(tmp_path):
    from engines.tts_cache import is_valid_tts_file, lookup_tts_cache, store_tts_cache

    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"xx")
    assert not is_valid_tts_file(bad)
    assert (
        store_tts_cache(bad, "t", "v", cache_dir=tmp_path / "c") is None
    )
    assert lookup_tts_cache("t", "v", cache_dir=tmp_path / "c") is None


def test_synthesize_parallel_uses_cache_and_skip(tmp_path, monkeypatch):
    from engines import tts_parallel as tp

    calls = {"n": 0}

    def fake_edge(text, voice, out_path, **kwargs):
        calls["n"] += 1
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"fake-audio-" + text.encode("utf-8")[:200] + b"x" * 300)

    monkeypatch.setattr(tp, "_synthesize_one_edge", fake_edge)

    items = [
        {
            "index": i,
            "text": f"Сегмент номер {i} для тесту.",
            "voice": "uk-UA-OstapNeural",
            "out_path": str(tmp_path / "out" / f"g{i}.mp3"),
            "rate": "-5%",
            "pitch": "",
        }
        for i in range(5)
    ]
    cache = tmp_path / "cache"
    res1, stats1 = tp.synthesize_segments_parallel(
        items,
        concurrency=4,
        cache_dir=cache,
        warmup=2,
        use_cache=True,
    )
    assert len(res1) == 5
    assert stats1["tts_cache_misses"] == 5
    assert calls["n"] == 5
    assert stats1["tts_concurrency_used"] >= 1

    # Second run → all cache hits, no Edge calls
    calls["n"] = 0
    items2 = [
        {**it, "out_path": str(tmp_path / "out2" / f"g{it['index']}.mp3")}
        for it in items
    ]
    res2, stats2 = tp.synthesize_segments_parallel(
        items2,
        concurrency=4,
        cache_dir=cache,
        warmup=2,
        use_cache=True,
    )
    assert stats2["tts_cache_hits"] == 5
    assert stats2["tts_cache_misses"] == 0
    assert calls["n"] == 0
    assert all(r.get("cache_hit") for r in res2)

    # Skip existing
    calls["n"] = 0
    res3, stats3 = tp.synthesize_segments_parallel(
        items2,
        concurrency=4,
        cache_dir=cache,
        warmup=0,
        use_cache=True,
        skip_existing=True,
    )
    assert stats3["tts_skips_existing"] == 5
    assert calls["n"] == 0


def test_resolve_concurrency_env(monkeypatch):
    from engines.tts_parallel import resolve_edge_tts_concurrency

    monkeypatch.setenv("EDGE_TTS_CONCURRENCY", "7")
    assert resolve_edge_tts_concurrency(None) == 7
    monkeypatch.setenv("EDGE_TTS_CONCURRENCY", "99")
    assert resolve_edge_tts_concurrency(None) == 8
    assert resolve_edge_tts_concurrency(5) == 5
