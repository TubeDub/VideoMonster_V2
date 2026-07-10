"""Tests — semantic meaning preservation (no tail clipping)."""

from __future__ import annotations

from engines.semantic_meaning import (
    apply_compact_phrases,
    is_truncated_adaptation,
    verify_meaning_preserved,
)
from engines.translation_adapt import adapt_translation_shorter


def test_is_truncated_detects_ellipsis():
    orig = (
        "Але, коли він їхав, Джордж-молодший не міг позбутися відчуття, "
        "що йому справді страшно їхати туди."
    )
    bad = "Але, коли він їхав, Джордж-молодший не міг позбутися відчуття…"
    assert is_truncated_adaptation(orig, bad)


def test_verify_meaning_rejects_truncation():
    src = (
        "But, as he was driving, George Jr. could not help but feel "
        "like he was really dreading actually getting there."
    )
    orig = (
        "Але, коли він їхав, Джордж-молодший не міг позбутися відчуття, "
        "що йому справді страшно їхати туди."
    )
    bad = "Але, коли він їхав, Джордж-молодший не міг позбутися відчуття…"
    ok, reason, _ = verify_meaning_preserved(src, orig, bad, target_lang="uk")
    assert not ok
    assert reason in ("truncated_tail", "incomplete_sentence", "preserved_token")


def test_filler_removal_not_flagged_as_truncation():
    """A big word drop that KEEPS the sentence ending is a rephrase, not a clip.

    Regression: the pure length-drop rule used to reject legitimate filler/
    synonym shortening, blocking successful LLM adaptation (TZ §1/§4).
    """
    orig = (
        "Він дуже справді просто надзвичайно втомився сьогодні, "
        "доволі сильно і вельми помітно для всіх навколо."
    )
    rephrased = "Він втомився сьогодні, сильно і помітно для всіх навколо."
    assert is_truncated_adaptation(orig, rephrased) is False
    ok, reason, _ = verify_meaning_preserved(
        "He was really very tired today.", orig, rephrased, target_lang="uk"
    )
    assert ok, f"legitimate rephrase rejected: {reason}"


def test_real_tail_clip_still_detected():
    """Ending actually lost → still truncation."""
    orig = "Він поїхав до магазину, купив молоко та хліб і повернувся додому."
    clipped = "Він поїхав до магазину."
    assert is_truncated_adaptation(orig, clipped) is True


def test_adapt_translation_shorter_never_returns_tail_clip():
    src = (
        "But, as he was driving, George Jr. could not help but feel "
        "like he was really dreading actually getting there."
    )
    long_uk = (
        "Але, коли він їхав, Джордж-молодший не міг позбутися відчуття, "
        "що йому справді страшно їхати туди, і це його дуже турбувало."
    )
    out = adapt_translation_shorter(
        long_uk,
        target_ratio=0.6,
        source_hint=src,
        allow_llm=False,
        stage="auto",
        tgt_lang="uk",
    )
    assert not is_truncated_adaptation(long_uk, out)
    assert "…" not in out


def test_apply_compact_phrases_uk():
    text = "Він не міг позбутися відчуття, що все погано."
    out = apply_compact_phrases(text, target_lang="uk")
    assert "позбутися відчуття" not in out
    assert "тривога" in out.lower() or len(out.split()) <= len(text.split())
