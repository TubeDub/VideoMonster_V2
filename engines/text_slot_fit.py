# -*- coding: utf-8 -*-
"""Happy Path text↔slot fit — paraphrase length, keep natural speech speed.

Priority (TZ): natural rate > meaning > timing.
atempo is a last resort (0.95–1.08); never chop words or pad dead silence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.text_slot_fit")

# Target band: predicted TTS within ±15% of slot.
FIT_TOLERANCE = 0.15
# TZ Stage 3: shorten when predicted > slot * 1.10
OVERFLOW_FIT_RATIO = 1.10
# Mild underfill: leave natural pause, do not slow voice / expand aggressively.
UNDERFILL_EXPAND_RATIO = 0.75
# Retention floors — severe overflow may drop more clauses (still no word-chop).
MIN_WORD_RETENTION = 0.55
MIN_WORD_RETENTION_SEVERE = 0.30
SEVERE_OVERFLOW_RATIO = 1.50


@dataclass
class TextFitResult:
    text: str
    slot_ms: int
    predicted_ms_before: int
    predicted_ms_after: int
    action: str = "none"  # none | shorten | expand | unchanged
    changed: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_tts_ms(text: str, lang: str = "uk") -> int:
    """Chars/syllables heuristic — no audio synthesis."""
    try:
        from engines.semantic_adaptation import estimate_tts_duration_ms

        return int(estimate_tts_duration_ms(text, lang) or 0)
    except Exception:
        t = str(text or "").strip()
        if not t:
            return 0
        lang0 = str(lang or "uk").split("-")[0].lower()
        cps = 12.0 if lang0 in ("uk", "ru", "be") else 14.0
        return int(max(200, (len(t) / cps) * 1000.0))


def _drop_parentheticals(text: str) -> str:
    out = re.sub(r"\([^)]{0,80}\)", " ", text)
    out = re.sub(r"\[[^\]]{0,80}\]", " ", out)
    return " ".join(out.split()).strip()


def _drop_redundant_clauses(text: str, lang: str) -> str:
    """Light rule shorteners — keep names/facts, drop discourse fluff."""
    out = text
    lang0 = str(lang or "uk").split("-")[0].lower()
    patterns: list[str]
    if lang0 == "uk":
        patterns = [
            r"\bвласне\s+кажучи\b",
            r"\bскажімо\s+так\b",
            r"\bяк\s+би\b",
            r"\bну\b",
            r"\bотже\b,",
            r"\bтож\b,",
        ]
    elif lang0 == "ru":
        patterns = [
            r"\bвообще(?:-то)?\b",
            r"\bкак\s+бы\b",
            r"\bну\b",
            r"\bитак\b,",
            r"\bтак\s+что\b,",
        ]
    else:
        patterns = [
            r"\breally\b",
            r"\bactually\b",
            r"\bbasically\b",
            r"\byou\s+know\b",
            r"\bkind\s+of\b",
            r"\bsort\s+of\b",
        ]
    for pat in patterns:
        out2 = re.sub(pat, "", out, flags=re.I)
        out2 = " ".join(out2.split()).strip(" ,;:")
        if out2 and len(out2) < len(out):
            out = out2
    out = out.replace(" — ", ", ").replace(" – ", ", ")
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r",\s*,+", ", ", out)
    return out.strip(" ,;")


def _trim_trailing_tail(text: str, budget_chars: int) -> str:
    """Drop trailing subordinate clause after comma/dash if still over budget."""
    t = text.strip()
    if len(t) <= budget_chars:
        return t
    for sep in (", який ", ", которая ", ", который ", ", that ", ", which ", " — ", " – "):
        idx = t.lower().rfind(sep.lower())
        if idx > int(len(t) * 0.45):
            cand = t[:idx].rstrip(" ,;—–")
            if cand and len(cand) >= int(len(t) * 0.55):
                return cand + ("." if not cand.endswith((".", "!", "?", "…")) else "")
    # Last resort: cut at last sentence boundary that still keeps ≥55%
    parts = re.split(r"(?<=[.!?…])\s+", t)
    if len(parts) >= 2:
        kept = []
        for p in parts:
            trial = " ".join(kept + [p]).strip()
            if len(trial) <= budget_chars or not kept:
                kept.append(p)
            else:
                break
        if kept:
            return " ".join(kept).strip()
    return t


def _safe_shorten(text: str, slot_ms: int, lang: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    out = " ".join(str(text or "").split()).strip()
    if not out:
        return out, reasons

    pred0 = estimate_tts_ms(out, lang)
    severe = bool(slot_ms > 0 and pred0 > int(slot_ms * SEVERE_OVERFLOW_RATIO))
    min_ret = MIN_WORD_RETENTION_SEVERE if severe else MIN_WORD_RETENTION
    if severe:
        reasons.append("severe_overflow")

    # Soft compress fillers (existing light helper).
    try:
        from engines.mt.tts_slot_compress import soft_compress_for_slot

        compressed = soft_compress_for_slot(out, slot_ms=slot_ms, target_lang=lang)
        if compressed and compressed != out:
            out = compressed
            reasons.append("soft_compress")
    except Exception:
        pass

    pred = estimate_tts_ms(out, lang)
    target = max(200, int(slot_ms * OVERFLOW_FIT_RATIO))
    if pred <= target:
        return out, reasons

    cleaned = _drop_parentheticals(out)
    if cleaned != out and cleaned:
        out = cleaned
        reasons.append("drop_parens")
        pred = estimate_tts_ms(out, lang)
        if pred <= target:
            return out, reasons

    trimmed = _drop_redundant_clauses(out, lang)
    if trimmed and trimmed != out:
        # Refuse destructive truncation vs original meaning.
        try:
            from engines.semantic_meaning import is_truncated_adaptation

            if not is_truncated_adaptation(text, trimmed):
                out = trimmed
                reasons.append("drop_fillers")
        except Exception:
            out = trimmed
            reasons.append("drop_fillers")
    pred = estimate_tts_ms(out, lang)
    if pred <= target:
        return out, reasons

    # Stronger meaning-safe shorten (existing soft_sync helper, no ADA/SSO).
    try:
        from engines.soft_sync import shorten_text_for_slot

        stronger = shorten_text_for_slot(
            out, slot_ms=slot_ms, lang=lang, source_hint=""
        )
        if stronger and stronger != out:
            ow = max(1, len(out.split()))
            nw = len(stronger.split())
            if nw >= int(ow * min_ret):
                try:
                    from engines.semantic_meaning import is_truncated_adaptation

                    if not is_truncated_adaptation(text, stronger):
                        out = stronger
                        reasons.append("soft_sync_shorten")
                except Exception:
                    out = stronger
                    reasons.append("soft_sync_shorten")
                pred = estimate_tts_ms(out, lang)
                if pred <= target:
                    return out, reasons
    except Exception:
        pass

    # Char budget ≈ slot at language cps
    lang0 = str(lang or "uk").split("-")[0].lower()
    cps = 12.0 if lang0 in ("uk", "ru", "be") else 14.0
    # Aim near slot (+10%); Edge-TTS variance handled by mild atempo ≤1.08.
    budget_chars = max(24, int((slot_ms / 1000.0) * cps * OVERFLOW_FIT_RATIO))
    orig_words = max(1, len(str(text or "").split()))
    tailed = _trim_trailing_tail(out, budget_chars)
    if tailed and tailed != out and len(tailed.split()) >= int(orig_words * min_ret):
        accept_tail = True
        try:
            from engines.semantic_meaning import is_truncated_adaptation

            if is_truncated_adaptation(text, tailed):
                accept_tail = False
        except Exception:
            pass
        if accept_tail:
            out = tailed
            reasons.append("trim_tail_clause")

    # Still over: keep leading sentences that fit.
    pred = estimate_tts_ms(out, lang)
    if pred > target:
        parts = re.split(r"(?<=[.!?…])\s+", out)
        if len(parts) >= 2:
            kept: list[str] = []
            for p in parts:
                trial = " ".join(kept + [p]).strip()
                if estimate_tts_ms(trial, lang) <= target or not kept:
                    kept.append(p)
                else:
                    break
            cand = " ".join(kept).strip()
            if (
                cand
                and cand != out
                and len(cand.split()) >= int(orig_words * min_ret)
            ):
                accept = True
                try:
                    from engines.semantic_meaning import is_truncated_adaptation

                    if is_truncated_adaptation(text, cand):
                        accept = False
                except Exception:
                    pass
                if accept:
                    out = cand
                    reasons.append("keep_leading_sentences")

    # Extreme overflow: keep leading sentences even if meaning-guard is strict.
    pred = estimate_tts_ms(out, lang)
    if pred > int(slot_ms * SEVERE_OVERFLOW_RATIO):
        parts = re.split(r"(?<=[.!?…])\s+", out)
        if len(parts) >= 2:
            kept2: list[str] = []
            for p in parts:
                trial = " ".join(kept2 + [p]).strip()
                if estimate_tts_ms(trial, lang) <= target or not kept2:
                    kept2.append(p)
                else:
                    break
            cand2 = " ".join(kept2).strip()
            if cand2 and cand2 != out and len(cand2.split()) >= 4:
                out = cand2
                reasons.append("severe_keep_leading")
        # Still too long: hard char budget cut at last space (no mid-word).
        pred = estimate_tts_ms(out, lang)
        if pred > int(slot_ms * SEVERE_OVERFLOW_RATIO) and budget_chars > 24:
            if len(out) > budget_chars:
                cut = out[:budget_chars].rsplit(" ", 1)[0].strip(" ,;:—–-")
                if cut and len(cut.split()) >= 4:
                    out = cut + ("." if not cut.endswith((".", "!", "?", "…")) else "")
                    reasons.append("severe_char_budget")
    return out, reasons


def _light_expand(text: str, slot_ms: int, lang: str) -> tuple[str, list[str]]:
    """Optional mild expand — no LLM required; skip if nothing safe."""
    # Keep short speech short; natural pause handles underfill.
    # Only expand tiny fragments that sound abrupt.
    t = str(text or "").strip()
    if not t or len(t.split()) > 8:
        return t, []
    pred = estimate_tts_ms(t, lang)
    if pred >= slot_ms * UNDERFILL_EXPAND_RATIO:
        return t, []
    # Do not invent content without source — leave as-is (pause is fine).
    return t, []


def fit_text_to_slot(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    source_hint: str = "",
    allow_expand: bool = True,
) -> TextFitResult:
    """One Happy Path step: rewrite length toward slot without changing speech rate."""
    original = " ".join(str(text or "").split()).strip()
    slot = max(0, int(slot_ms or 0))
    before = estimate_tts_ms(original, lang)
    if not original or slot <= 0:
        return TextFitResult(
            text=original,
            slot_ms=slot,
            predicted_ms_before=before,
            predicted_ms_after=before,
            action="none",
            changed=False,
        )

    lo = int(slot * (1.0 - FIT_TOLERANCE))
    hi = int(slot * OVERFLOW_FIT_RATIO)
    reasons: list[str] = []
    out = original
    action = "unchanged"

    if before > hi:
        out, reasons = _safe_shorten(original, slot, lang)
        action = "shorten" if out != original else "unchanged"
    elif allow_expand and before < int(slot * UNDERFILL_EXPAND_RATIO):
        out, reasons = _light_expand(original, slot, lang)
        action = "expand" if out != original else "unchanged"
    else:
        action = "unchanged"

    after = estimate_tts_ms(out, lang)
    # If shorten made it worse / empty — revert.
    if not out.strip():
        out = original
        after = before
        action = "unchanged"
        reasons = ["reverted_empty"]
    elif action == "shorten" and after > before and after > hi:
        out = original
        after = before
        action = "unchanged"
        reasons = ["reverted_worse"]

    result = TextFitResult(
        text=out,
        slot_ms=slot,
        predicted_ms_before=before,
        predicted_ms_after=after,
        action=action,
        changed=out != original,
        reasons=reasons,
    )
    if result.changed:
        logger.info(
            "fit_text_to_slot: action=%s slot=%d pred %d→%d reasons=%s",
            action,
            slot,
            before,
            after,
            reasons,
        )
    return result


def fit_segments_to_slots(
    texts: list[str],
    timing_map: list[Any] | None,
    *,
    lang: str = "uk",
    source_hints: list[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Apply ``fit_text_to_slot`` to each segment; return texts + audit rows."""
    out: list[str] = []
    audits: list[dict[str, Any]] = []
    tm = list(timing_map or [])
    for i, raw in enumerate(texts):
        slot = 0
        if i < len(tm):
            item = tm[i]
            if isinstance(item, dict):
                slot = max(0, int(item.get("end", 0)) - int(item.get("start", 0)))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                slot = max(0, int(item[1]) - int(item[0]))
        hint = ""
        if source_hints and i < len(source_hints):
            hint = str(source_hints[i] or "")
        fit = fit_text_to_slot(str(raw or ""), slot, lang, source_hint=hint)
        out.append(fit.text)
        row = fit.to_dict()
        row["idx"] = i
        audits.append(row)
    return out, audits
