# -*- coding: utf-8 -*-
"""Stage 33 — diagnostic d3b6fe76: Russian leak in EN→UK dub.

Zip metrics this suite locks:
- STUDIO RuntimeError language_mismatch expected=uk detected=ru
- hard fail idx 27 «Да. Джонатан, ты кажется умным.»
- recovery only swapped это→це / appended «саме тоді» (false heal)
- audio_exists=51/51, atempo 0.92–1.08, edge-offline / uk-UA-OstapNeural
- Cyrillic ratio 1.0 so Stage 29 gate voiced Russian
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ZIP_IDX27_RU = "Да. Джонатан, ты кажется умным."
ZIP_IDX27_EN = "Yeah. Jonathan, you seem smart."
ZIP_IDX27_UK = "Так. Джонатане, ти здаєшся розумним."
ZIP_IDX0_MIXED = "Эй, мужик, ты отлично справляешься. Ось долар, саме тоді."
ZIP_FALSE_HEAL = "Честно говоря, це много, саме тоді."
UK_CLEAN = "Привіт усім друзям сьогодні, це український текст."


def test_russian_line_is_not_uk_tts_ok():
    from engines.tts_lang_lock import is_uk_tts_text_ok, uk_text_has_russian_leak

    assert uk_text_has_russian_leak(ZIP_IDX27_RU)
    assert not is_uk_tts_text_ok(ZIP_IDX27_RU)
    assert uk_text_has_russian_leak(ZIP_IDX0_MIXED)
    assert not is_uk_tts_text_ok(ZIP_IDX0_MIXED)
    assert uk_text_has_russian_leak(ZIP_FALSE_HEAL)
    assert not is_uk_tts_text_ok(ZIP_FALSE_HEAL)
    assert not uk_text_has_russian_leak(UK_CLEAN)
    assert is_uk_tts_text_ok(UK_CLEAN)


def test_ruism_rewrite_clears_zip_idx27():
    from engines.tts_lang_lock import (
        is_uk_tts_text_ok,
        rewrite_russian_leak_for_uk,
        uk_text_has_russian_leak,
    )

    out = rewrite_russian_leak_for_uk(ZIP_IDX27_RU)
    assert out != ZIP_IDX27_RU
    assert not uk_text_has_russian_leak(out), out
    assert is_uk_tts_text_ok(out)
    assert "ты" not in out
    assert "умным" not in out


def test_guard_rewrites_russian_without_source():
    from engines.tts_lang_lock import guard_uk_tts_text

    out, meta = guard_uk_tts_text(
        ZIP_IDX27_RU,
        source_text="",
        allow_remt=False,
        segment_index=27,
    )
    assert meta.get("tts_lang_ok") is True
    assert meta.get("ruism_rewrite") is True
    assert "ты" not in out
    assert out


def test_recovery_remt_heals_idx27_no_hard_fail():
    from engines.language_validation.recovery import apply_recovery_and_revalidate

    segs = [
        {
            "text": ZIP_IDX27_RU,
            "plain_text": ZIP_IDX27_RU,
            "tts_text": ZIP_IDX27_RU,
            "segment_id": "3c61a6355b844c6f98336e8556475c0b",
            "index": 0,
        }
    ]
    with patch(
        "engines.tts_lang_lock.force_remt_segment_no_cache",
        return_value=ZIP_IDX27_UK,
    ):
        result = apply_recovery_and_revalidate(
            segs,
            source_segments=[ZIP_IDX27_EN],
            target_lang="uk",
            source_lang="en",
            stage="STUDIO",
        )
    assert result["failed_hard"] == 0, result
    assert 0 in result["healed_indices"]
    text = segs[0]["text"]
    assert "ты" not in text
    assert "умным" not in text
    assert "здаєшся" in text or "розумн" in text


def test_glue_false_heal_does_not_count_as_uk():
    """Zip idx 33: «це» + «саме тоді» fooled the classifier; still Russian."""
    from engines.tts_lang_lock import uk_text_has_russian_leak

    assert uk_text_has_russian_leak(ZIP_FALSE_HEAL)


def test_recovery_skip_tts_when_remt_and_rewrite_still_russian():
    from engines.language_validation.recovery import apply_recovery_and_revalidate

    stubborn = "Да. Джонатан, ты кажется умным. Эй, мужик."
    segs = [
        {
            "text": stubborn,
            "plain_text": stubborn,
            "segment_id": "idx27",
            "index": 0,
            "file": r"C:\fake\tts_ru.mp3",
            "resolved_path": r"C:\fake\tts_ru.mp3",
        }
    ]
    with patch(
        "engines.tts_lang_lock.force_remt_segment_no_cache",
        return_value=stubborn,
    ), patch(
        "engines.tts_lang_lock.rewrite_russian_leak_for_uk",
        return_value=stubborn,
    ), patch(
        "engines.translation_naturalizer.naturalize_text",
        side_effect=lambda text, *_a, **_k: text,
    ):
        result = apply_recovery_and_revalidate(
            segs,
            source_segments=[ZIP_IDX27_EN],
            target_lang="uk",
            source_lang="en",
            stage="STUDIO",
        )
    assert result["failed_hard"] == 0, result
    assert segs[0].get("tts_skip_reason") == "russian_in_uk"
    assert segs[0].get("skip_tts") is True
    assert segs[0].get("file") in (None, "")


def test_synthesize_refuses_russian_uk_target(tmp_path):
    from engines import tts_backends
    from engines.tts_engines import registry

    called = {"n": 0}

    def _fake(*_a, **_k):
        called["n"] += 1
        raise AssertionError("must not synth Russian as UK")

    real = registry.synthesize
    registry.synthesize = _fake
    try:
        result = tts_backends.synthesize_with_backend(
            ZIP_IDX27_RU,
            "uk-UA-OstapNeural",
            str(tmp_path / "ru.wav"),
            engine_id="edge-offline",
            target_lang="uk",
        )
    finally:
        registry.synthesize = real

    assert result.ok is False
    assert called["n"] == 0
    assert "russian" in str(result.error or "").lower() or (
        result.meta or {}
    ).get("tts_skip_reason") == "russian_in_uk"
