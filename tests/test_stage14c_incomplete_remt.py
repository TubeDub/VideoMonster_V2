# -*- coding: utf-8 -*-
"""Stage 14c — remt truncated MT (George Jr. #5 crash/survived)."""

from __future__ import annotations

from pathlib import Path

from engines.mt.oversized_guard import OversizedSplitResult, split_oversized_unit
from engines.mt_batch import translate_segments_batch
from engines.mt_cache import is_incomplete_mt_pair

SEG5_EN = (
    "Two weeks later George Jr. was laying in a hospital bed in the intensive "
    "care unit at the local hospital. So two weeks earlier when George was "
    "making that turn and then something happened, well what it was is another "
    "car came speeding down the road and smashed into George's car so hard "
    "that George Jr. was ejected from the car but he had survived."
)


def _has_crash_entity(text: str) -> bool:
    low = (text or "").lower()
    return any(n in low for n in ("вижив", "розбив", "викину", "аварі"))


def test_seg5_splits_by_max_words():
    parts = split_oversized_unit(SEG5_EN)
    assert len(parts) >= 2
    assert any("smashed" in p.lower() or "survived" in p.lower() for p in parts)
    assert all(len(p.split()) <= 55 for p in parts)


def test_short_hospital_only_is_incomplete():
    short = (
        "Через два тижні Джордж молодший лежав у лікарняному ліжку "
        "у реанімації місцевої лікарні."
    )
    assert is_incomplete_mt_pair(SEG5_EN, short, "en", "uk")


def test_split_path_keeps_crash_entities(tmp_path: Path, monkeypatch):
    """With max_words-aware split, rejoined Marian units keep survived/smash."""
    monkeypatch.setenv("VM_MT_SKIP_CACHE_LONG", "1")

    def fake_marian(texts, src, tgt, *, app_dir):
        out = []
        for t in texts:
            tl = t.lower()
            if "smashed" in tl or "survived" in tl or "ejected" in tl:
                out.append(
                    (
                        "інша машина розбила авто так сильно що Джорджа викинуло "
                        "але він вижив " + " ".join(["слово"] * 20),
                        {"engine": "marian"},
                    )
                )
            elif "hospital" in tl or "two weeks later" in tl:
                out.append(
                    (
                        "Через два тижні Джордж молодший лежав у лікарні "
                        + " ".join(["слово"] * 10),
                        {"engine": "marian"},
                    )
                )
            else:
                out.append(
                    (
                        " ".join(["слово"] * max(12, len(t.split()))),
                        {"engine": "marian"},
                    )
                )
        return out

    monkeypatch.setattr("engines.mt_batch._try_marian_batch", fake_marian)
    out, _st = translate_segments_batch(
        [SEG5_EN],
        "en",
        "uk",
        cache_dir=tmp_path,
        prefer_marian=True,
        concurrency=1,
    )
    assert _has_crash_entity(out[0])
    assert not is_incomplete_mt_pair(SEG5_EN, out[0], "en", "uk")


def test_incomplete_remt_when_batch_collapses(tmp_path: Path, monkeypatch):
    """If first pass returns hospital-only, sentence remt recovers entities."""
    monkeypatch.setenv("VM_MT_SKIP_CACHE_LONG", "1")

    def no_split(segments, *, log=True):
        texts = [" ".join(str(s or "").split()) for s in segments]
        return OversizedSplitResult(
            texts=texts,
            parent_indices=list(range(len(texts))),
            split_count=0,
        )

    monkeypatch.setattr(
        "engines.mt.oversized_guard.guard_segments_before_mt", no_split
    )

    def fake_marian(texts, src, tgt, *, app_dir):
        # Whole-segment collapse
        if len(texts) == 1 and len(texts[0].split()) > 50:
            return [
                (
                    "Через два тижні Джордж молодший лежав у лікарні.",
                    {"engine": "marian"},
                )
            ]
        out = []
        for t in texts:
            tl = t.lower()
            if "smashed" in tl or "survived" in tl or "ejected" in tl:
                out.append(
                    (
                        "машина розбила авто Джорджа його викинуло але він вижив "
                        + " ".join(["слово"] * 22),
                        {"engine": "marian"},
                    )
                )
            else:
                out.append(
                    (
                        "Через два тижні Джордж молодший лежав у лікарні "
                        + " ".join(["слово"] * 12),
                        {"engine": "marian"},
                    )
                )
        return out

    monkeypatch.setattr("engines.mt_batch._try_marian_batch", fake_marian)

    def no_real_mt(*_a, **_k):
        raise RuntimeError("real MT must not run in unit test")

    monkeypatch.setattr("engines.mt_batch._translate_one_traced", no_real_mt)

    out, st = translate_segments_batch(
        [SEG5_EN],
        "en",
        "uk",
        cache_dir=tmp_path,
        prefer_marian=True,
        concurrency=1,
    )
    assert _has_crash_entity(out[0])
    assert not is_incomplete_mt_pair(SEG5_EN, out[0], "en", "uk")
    assert int(st.get("mt_incomplete_remts") or 0) >= 1