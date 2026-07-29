# -*- coding: utf-8 -*-
"""Stage 12 — lang lock + incomplete 0.55 + long skip-cache."""

from __future__ import annotations

from pathlib import Path

from engines.mt_batch import translate_segments_batch
from engines.mt_cache import (
    is_incomplete_mt_pair,
    lookup_mt_cache,
    skip_cache_for_long_segments,
    store_mt_cache,
)
from engines.simple_voice_lock import lock_simple_pipeline_voice, resolve_pipeline_voice
from engines.tts_lang_lock import (
    cyrillic_letter_ratio,
    guard_uk_tts_text,
    is_uk_tts_text_ok,
)


def test_incomplete_ratio_055():
    src = " ".join([f"word{i}" for i in range(40)])  # >30
    # 21/40 = 0.525 < 0.55 → incomplete
    short = " ".join(["слово"] * 21)
    assert is_incomplete_mt_pair(src, short, "en", "uk")
    ok = " ".join(["слово"] * 23)  # 0.575
    assert not is_incomplete_mt_pair(src, ok, "en", "uk")


def test_incomplete_smash_survived_star_wars():
    src = (
        "He survived the smash when he was ejected and never raced "
        "race cars anymore until George Lucas made Star Wars."
    )
    bad = "Він потрапив у біду і більше не їздив."
    assert is_incomplete_mt_pair(src, bad, "en", "uk")
    good = (
        "Він вижив після аварії, коли його викинуло, і більше не ганяв "
        "на гоночних автомобілях, доки Джордж Лукас не зняв Зоряні війни."
    )
    assert not is_incomplete_mt_pair(src, good, "en", "uk")


def test_reject_non_cyrillic_for_uk_tts():
    czech = "Vítejme u další epizody našeho pořadu."
    assert cyrillic_letter_ratio(czech) < 0.6
    assert not is_uk_tts_text_ok(czech)
    out, meta = guard_uk_tts_text(
        czech,
        source_text="",
        allow_remt=False,
        segment_index=1,
    )
    assert out == ""
    assert meta.get("rejected_non_target")
    assert meta.get("skipped")
    uk = "Вітаємо на наступному епізоді нашої програми."
    assert is_uk_tts_text_ok(uk)


def test_long_skip_cache_default_on(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("VM_MT_NO_CACHE", raising=False)
    monkeypatch.setenv("VM_MT_SKIP_CACHE_LONG", "1")
    assert skip_cache_for_long_segments() is True

    long = " ".join([f"word{i}" for i in range(60)])
    # Plant a full-looking cache entry that would otherwise hit
    full = " ".join(["слово"] * 60)
    store_mt_cache(long, full, "en", "uk", cache_dir=tmp_path)
    assert lookup_mt_cache(long, "en", "uk", cache_dir=tmp_path) == full

    calls = {"n": 0}

    def fake_marian(texts, src, tgt, *, app_dir):
        calls["n"] += 1
        return [(" ".join(["слово"] * max(12, len(t.split()))), {"engine": "marian"}) for t in texts]

    monkeypatch.setattr("engines.mt_batch._try_marian_batch", fake_marian)
    out, st = translate_segments_batch(
        [long],
        "en",
        "uk",
        cache_dir=tmp_path,
        prefer_marian=True,
        concurrency=1,
    )
    assert st.get("mt_long_cache_skips", 0) >= 1
    assert st["mt_cache_misses"] >= 1
    assert calls["n"] >= 1
    assert out[0]
    assert st["mt_segment_engines"][0] == "marian_batch"


def test_voice_lock_rejects_czech():
    info = {"target_lang": "uk", "simple_pipeline": True}
    v = resolve_pipeline_voice(info, fallback="cs-CZ-AntoninNeural")
    assert v.startswith("uk-UA-")
    segs = [{"text": "Привіт", "assigned_voice": "cs-CZ-AntoninNeural"}]
    stamp = lock_simple_pipeline_voice(segs, pipeline_voice="cs-CZ-AntoninNeural", task_info=info)
    assert stamp["pipeline_voice"].startswith("uk-UA-")
    assert stamp["unique_voices_used"] == 1
    assert segs[0]["assigned_voice"].startswith("uk-UA-")
