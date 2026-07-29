# -*- coding: utf-8 -*-
"""Stage 10b — verify beams / glossary / short-cache wiring in Marian path."""

from __future__ import annotations

import ast
from pathlib import Path

from engines.mt.glossary_en_uk import (
    apply_post_mt_glossary_fixes,
    protect_glossary,
    restore_glossary,
)
from engines.mt.stable_translate import resolve_marian_beams
from engines.mt_cache import (
    is_incomplete_mt_pair,
    lookup_mt_cache,
    store_mt_cache,
)

ROOT = Path(__file__).resolve().parents[1]
STABLE = ROOT / "engines" / "mt" / "stable_translate.py"


def test_no_legacy_beams_one_ternary():
    """Must not contain: num_beams = 1 if use_stable_mt() else 4."""
    src = STABLE.read_text(encoding="utf-8")
    assert "1 if use_stable_mt()" not in src
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "resolve_marian_beams" in names


def test_beams_default_two_and_env(monkeypatch):
    monkeypatch.delenv("MT_NUM_BEAMS", raising=False)
    monkeypatch.delenv("VM_MT_NUM_BEAMS", raising=False)
    assert resolve_marian_beams(simple=True) == 2
    monkeypatch.setenv("MT_NUM_BEAMS", "1")
    assert resolve_marian_beams(simple=True) == 1
    monkeypatch.setenv("VM_MT_NUM_BEAMS", "3")
    monkeypatch.delenv("MT_NUM_BEAMS", raising=False)
    assert resolve_marian_beams(simple=True) == 3
    monkeypatch.setenv("MT_NUM_BEAMS", "99")
    assert resolve_marian_beams(simple=True) == 4


def test_glossary_fiat_to_fiat_uk():
    src = "his father bought him a small Italian car called the Fiat."
    protected, forms = protect_glossary(src)
    assert "Fiat" not in protected
    restored = restore_glossary(protected, forms)
    assert "Фіат" in restored
    assert "Фіат" in apply_post_mt_glossary_fixes("купив Файта.")
    assert "Файта" not in apply_post_mt_glossary_fixes("купив Файта.")


def test_short_cache_rejected_words_gt_55(tmp_path: Path):
    # 56+ English words, short UK → incomplete even without sentence oversized.
    src = " ".join([f"word{i}" for i in range(60)])
    short = "короткий текст"
    assert len(src.split()) > 55
    assert is_incomplete_mt_pair(src, short, "en", "uk")
    assert (
        store_mt_cache(src, short, "en", "uk", engine="auto", cache_dir=tmp_path)
        is None
    )
    # Manually plant a bad cache file, then lookup must miss + delete.
    from engines.mt_cache import cache_path_for_key, mt_cache_key
    import json

    key = mt_cache_key(src, "en", "uk", engine="auto")
    path = cache_path_for_key(tmp_path, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source": src,
                "translated": short,
                "source_lang": "en",
                "target_lang": "uk",
                "engine": "auto",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert lookup_mt_cache(src, "en", "uk", engine="auto", cache_dir=tmp_path) is None
    assert not path.is_file()


def test_stable_has_glossary_protect_restore():
    src = STABLE.read_text(encoding="utf-8")
    assert "protect_glossary" in src
    assert "restore_glossary" in src or "_finish_en_uk_glossary" in src
    assert "apply_post_mt_glossary_fixes" in src
    assert "resolve_marian_beams" in src
