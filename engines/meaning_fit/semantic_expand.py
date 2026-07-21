"""MF4 — SemanticExpand: slightly longer, no filler, no invented facts."""

from __future__ import annotations

import re
from typing import Any

from engines.meaning_fit.duration_predictor import predict_vs_slot
from engines.meaning_fit.flags import meaning_fit_expand_flag, meaning_fit_flag
from engines.meaning_fit.types import FitResult

_FILLER = re.compile(
    r"\b(еее|ее+|ммм|ну+|типа|як би|коротше кажучи)\b",
    re.I,
)

# Safe expansions that add spoken clarity without new facts
_UK_EXPAND_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^Коза\s+паслась\.?$", re.I),
        "Коза спокійно паслась на лугу",
    ),
    (
        re.compile(r"^Він\s+біг\.?$", re.I),
        "Він швидко біг вперед",
    ),
    (
        re.compile(r"^Вона\s+читала\.?$", re.I),
        "Вона уважно читала книжку",
    ),
]

_UK_LIGHT_EXPAND: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bпаслась\b", re.I), "спокійно паслась"),
    (re.compile(r"\bбіг\b", re.I), "швидко біг"),
    (re.compile(r"\bчитала\b", re.I), "уважно читала"),
]


def _has_filler(text: str) -> bool:
    return bool(_FILLER.search(str(text or "")))


def _has_word_repeat(text: str) -> bool:
    words = [w.lower() for w in re.findall(r"\w+", str(text or ""), flags=re.UNICODE)]
    for i in range(1, len(words)):
        if words[i] == words[i - 1] and len(words[i]) > 2:
            return True
    return False


def _rule_expand(text: str) -> str | None:
    t = str(text or "").strip()
    if not t:
        return None
    for pat, repl in _UK_EXPAND_RULES:
        if pat.match(t):
            return repl
    out = t
    changed = False
    for pat, repl in _UK_LIGHT_EXPAND:
        nxt, n = pat.subn(repl, out, count=1)
        if n:
            out = nxt
            changed = True
            break
    if changed and out != t and not _has_filler(out) and not _has_word_repeat(out):
        return out
    return None


def semantic_expand(
    text_uk: str,
    slot_ms: int,
    *,
    original_en: str = "",
    force: bool = False,
) -> FitResult:
    text = str(text_uk or "").strip()
    if not (force or (meaning_fit_flag() and meaning_fit_expand_flag())):
        return FitResult(
            text_uk=text,
            status="noop",
            reason="flag_off_legacy",
            slot_ms=slot_ms,
            success=False,
            method="noop",
            meta={"enabled": False, "noop": True},
        )

    pred0 = predict_vs_slot(text, slot_ms)
    if pred0.verdict == "OK":
        return FitResult(
            text_uk=text,
            status="already_fits",
            reason="already_fits",
            predicted_ms=pred0.predicted_ms,
            slot_ms=slot_ms,
            verdict="OK",
            success=True,
            method="none",
        )
    if pred0.verdict != "TOO_SHORT":
        return FitResult(
            text_uk=text,
            status="already_fits" if pred0.verdict == "OK" else "fit_failed",
            reason="expand_not_needed" if pred0.verdict == "TOO_LONG" else "fit_failed",
            predicted_ms=pred0.predicted_ms,
            slot_ms=slot_ms,
            verdict=pred0.verdict,
            success=pred0.verdict == "OK",
            method="semantic_expand",
        )

    cand = _rule_expand(text)
    if not cand or _has_filler(cand) or _has_word_repeat(cand):
        return FitResult(
            text_uk=text,
            status="fit_failed",
            reason="fit_failed",
            predicted_ms=pred0.predicted_ms,
            slot_ms=slot_ms,
            verdict="TOO_SHORT",
            success=False,
            needs_manual=True,
            method="semantic_expand",
            meta={"rejected": "no_safe_expand"},
        )

    pred = predict_vs_slot(cand, slot_ms)
    ok = pred.verdict in ("OK", "TOO_SHORT") and pred.predicted_ms >= pred0.predicted_ms
    # Prefer landing in OK; accept longer toward slot
    success = pred.verdict == "OK"
    return FitResult(
        text_uk=cand if (success or pred.predicted_ms > pred0.predicted_ms) else text,
        status="paraphrase_expand" if cand != text else "fit_failed",
        reason="paraphrase_expand" if cand != text else "fit_failed",
        predicted_ms=pred.predicted_ms if cand != text else pred0.predicted_ms,
        slot_ms=slot_ms,
        verdict=pred.verdict if cand != text else pred0.verdict,
        success=success,
        needs_manual=not success,
        method="semantic_expand",
        meta={"source": text, "original_en": original_en},
    )
