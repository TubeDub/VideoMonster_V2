# -*- coding: utf-8 -*-
"""Job translate cache must not serve truncated / long-source blobs."""

from __future__ import annotations

from pathlib import Path

from engines.pipeline_cache import (
    load_translate_cache,
    save_translate_cache,
    translate_job_cache_acceptable,
)


MEGA = (
    "Two weeks later George Jr. was laying in a hospital bed in the intensive "
    "care unit at the local hospital. So two weeks earlier when George was making "
    "that turn and then something happened, well what it was is another car came "
    "and smashed into him and he was ejected and somehow survived."
)
SHORT_UK = "Через два тижні Джордж лежав у лікарні."


def test_job_cache_rejects_incomplete_pair():
    ok, reason = translate_job_cache_acceptable(
        [MEGA], [SHORT_UK], "en", "uk"
    )
    assert ok is False
    assert "incomplete" in reason or "long" in reason


def test_job_cache_rejects_long_even_if_full_looking():
    long = " ".join([f"word{i}" for i in range(60)])
    full = " ".join(["слово"] * 60)
    ok, reason = translate_job_cache_acceptable([long], [full], "en", "uk")
    assert ok is False
    assert reason.startswith("long_seg")


def test_load_translate_cache_miss_on_truncated(tmp_path: Path, monkeypatch):
    # Point pipeline cache root at tmp
    import engines.pipeline_cache as pc

    monkeypatch.setattr(pc, "_cache_root", lambda app_dir: tmp_path)

    sources = [MEGA, "Hello short."]
    bad = [SHORT_UK, "Привіт."]
    # Force-write raw blob bypassing save gate
    key = pc.segments_fingerprint(sources, "en", "uk")
    path = tmp_path / "translate" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        __import__("json").dumps(
            {
                **pc.cache_versions(),
                "segments": bad,
                "src": "en",
                "tgt": "uk",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert load_translate_cache(tmp_path, sources, "en", "uk") is None
    assert not path.is_file()  # unlinked on reject


def test_save_translate_cache_skips_incomplete(tmp_path: Path, monkeypatch):
    import engines.pipeline_cache as pc

    monkeypatch.setattr(pc, "_cache_root", lambda app_dir: tmp_path)
    save_translate_cache(
        tmp_path, [MEGA], "en", "uk", [SHORT_UK], route_label="test"
    )
    assert list((tmp_path / "translate").glob("*.json")) == []
