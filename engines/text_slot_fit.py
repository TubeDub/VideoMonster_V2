# -*- coding: utf-8 -*-
"""Happy Path text↔slot fit — paraphrase length, keep natural speech speed.

Priority (TZ Stage 4): natural rate > meaning > timing.
atempo is a last resort (0.95–1.08); never chop words or mid-thought tails.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.text_slot_fit")

# Target band: predicted TTS within ±15% of slot.
FIT_TOLERANCE = 0.15
# Stage 4: shorten when predicted > slot * 1.08
OVERFLOW_FIT_RATIO = 1.08
# Mild underfill: leave natural pause, do not slow voice / expand aggressively.
UNDERFILL_EXPAND_RATIO = 0.75
MIN_WORD_RETENTION = 0.55
MIN_WORD_RETENTION_SEVERE = 0.35
SEVERE_OVERFLOW_RATIO = 1.50

# Incomplete thought endings that must NEVER be voiced as a final cut.
_BAD_TAIL = re.compile(
    r"(?i)(?:\bй\s+застосувати|\bта\s+застосувати|\bі\s+застосувати|"
    r"\bнезважаючи\s+на\s+те|\bнесмотря\s+на\s+(?:это|то)|"
    r"\bdespite\s+(?:that|this)|\bin\s+spite\s+of\s+that|"
    r"\bвирішив|\bрешил|\bdecided|\bbegan|\bstarted|"
    r"\bщоб\s*$|\bале\s*$|\bі\s*$|\bта\s*$|\bщо\s*$|\bякий\s*$|\bяка\s*$|"
    r"\bякі\s*$|\bколи\s*$|\bтому\s*$|\bдля\s*$|\bпро\s*$|"
    r"\band\s*$|\bbut\s*$|\bto\s*$|\bthe\s*$|\ba\s*$)$"
)
_COMPLETE_END = re.compile(r"[.!?…»\"')\]]\s*$")
_DANGLING_CLAUSE = re.compile(
    r"(?i)(?:"
    r",\s*незважаючи\s+на\s+те|"
    r",\s*несмотря\s+на\s+(?:это|то)|"
    r",\s*despite\s+(?:that|this)|"
    r"\b(?:він|вона|вони|я|ти|ми|ви|he|she|they|i|we|you)\s+"
    r"(?:вирішив|вирішила|вирішили|решил|решила|решили|decided|began|started)"
    r")[.!?…]*$"
)


@dataclass
class TextFitResult:
    text: str
    slot_ms: int
    predicted_ms_before: int
    predicted_ms_after: int
    action: str = "none"  # none | shorten | expand | unchanged
    changed: bool = False
    reasons: list[str] = field(default_factory=list)
    meaning_truncated: bool = False

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
    if lang0 == "uk":
        patterns = [
            r"\bвласне\s+кажучи\b",
            r"\bскажімо\s+так\b",
            r"\bяк\s+би\b",
            r"\bну\b",
            r"\bотже\b,",
            r"\bтож\b,",
            r"\bдійсно\b",
            r"\bнасправді\b",
            r"\bпрактично\b",
            r"\bдуже\b",
        ]
    elif lang0 == "ru":
        patterns = [
            r"\bвообще(?:-то)?\b",
            r"\bкак\s+бы\b",
            r"\bну\b",
            r"\bитак\b,",
            r"\bтак\s+что\b,",
            r"\bдействительно\b",
            r"\bочень\b",
        ]
    else:
        patterns = [
            r"\breally\b",
            r"\bactually\b",
            r"\bbasically\b",
            r"\byou\s+know\b",
            r"\bkind\s+of\b",
            r"\bsort\s+of\b",
            r"\bvery\b",
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


def _is_complete_thought(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t.split()) < 3:
        return False
    # Strip trailing punct for dangling-clause checks.
    core = re.sub(r"[.!?…»\"')\]]+\s*$", "", t).strip()
    if _BAD_TAIL.search(core) or _BAD_TAIL.search(t):
        return False
    if _DANGLING_CLAUSE.search(t) or _DANGLING_CLAUSE.search(core):
        return False
    if _COMPLETE_END.search(t):
        return True
    # Allow short declarative without period if not an open conjunction.
    return not re.search(r"(?i)\b(але|і|та|що|коли|щоб|and|but|that|when)\s*$", t)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", str(text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _keep_leading_complete_sentences(
    text: str,
    slot_ms: int,
    lang: str,
    *,
    min_words: int,
) -> tuple[str, bool]:
    """Drop trailing full sentences until near slot; never leave a truncated clause."""
    parts = _split_sentences(text)
    if len(parts) < 2:
        return text, False
    target = max(200, int(slot_ms * OVERFLOW_FIT_RATIO))
    kept: list[str] = []
    for p in parts:
        trial = " ".join(kept + [p]).strip()
        if estimate_tts_ms(trial, lang) <= target or not kept:
            kept.append(p)
        else:
            break
    cand = " ".join(kept).strip()
    # Drop a trailing incomplete sentence if keep stopped mid-thought.
    while kept and not _is_complete_thought(" ".join(kept).strip()):
        kept.pop()
        cand = " ".join(kept).strip()
    if (
        cand
        and cand != text
        and len(cand.split()) >= min_words
        and _is_complete_thought(cand)
    ):
        return cand, False
    return text, False


def _paraphrase_compress(text: str, lang: str) -> str:
    """Whole-clause compress: drop appositions / relative tails safely."""
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return t
    # Drop trailing relative clauses after comma if remainder is a complete thought.
    for sep in (
        ", який ",
        ", яка ",
        ", які ",
        ", що ",
        ", чтобы ",
        ", that ",
        ", which ",
        ", who ",
        " — ",
        " – ",
    ):
        idx = t.lower().rfind(sep.lower())
        if idx > int(len(t) * 0.40):
            cand = t[:idx].rstrip(" ,;—–")
            if cand and _is_complete_thought(cand + ("." if not _COMPLETE_END.search(cand) else "")):
                if not cand.endswith((".", "!", "?", "…")):
                    cand = cand + "."
                if len(cand.split()) >= max(4, int(len(t.split()) * 0.35)):
                    return cand
    return t


def _safe_shorten(text: str, slot_ms: int, lang: str) -> tuple[str, list[str], bool]:
    """Shorten by paraphrase / full sentences only. Never hard-cut mid-thought.

    Returns (text, reasons, meaning_truncated). meaning_truncated stays False —
    hard-cuts are refused rather than applied.
    """
    reasons: list[str] = []
    meaning_truncated = False
    out = " ".join(str(text or "").split()).strip()
    if not out:
        return out, reasons, False

    pred0 = estimate_tts_ms(out, lang)
    severe = bool(slot_ms > 0 and pred0 > int(slot_ms * SEVERE_OVERFLOW_RATIO))
    min_ret = MIN_WORD_RETENTION_SEVERE if severe else MIN_WORD_RETENTION
    if severe:
        reasons.append("severe_overflow")
    orig_words = max(1, len(out.split()))
    target = max(200, int(slot_ms * OVERFLOW_FIT_RATIO))

    try:
        from engines.mt.tts_slot_compress import soft_compress_for_slot

        compressed = soft_compress_for_slot(out, slot_ms=slot_ms, target_lang=lang)
        if (
            compressed
            and compressed != out
            and _is_complete_thought(compressed)
        ):
            out = compressed
            reasons.append("soft_compress")
    except Exception:
        pass

    pred = estimate_tts_ms(out, lang)
    if pred <= target:
        return out, reasons, False

    cleaned = _drop_parentheticals(out)
    if cleaned != out and cleaned and _is_complete_thought(cleaned):
        out = cleaned
        reasons.append("drop_parens")
        pred = estimate_tts_ms(out, lang)
        if pred <= target:
            return out, reasons, False

    trimmed = _drop_redundant_clauses(out, lang)
    if trimmed and trimmed != out and _is_complete_thought(trimmed):
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
        return out, reasons, False

    para = _paraphrase_compress(out, lang)
    if para != out and _is_complete_thought(para):
        out = para
        reasons.append("clause_paraphrase")
        pred = estimate_tts_ms(out, lang)
        if pred <= target:
            return out, reasons, False

    try:
        from engines.soft_sync import shorten_text_for_slot

        stronger = shorten_text_for_slot(
            out, slot_ms=slot_ms, lang=lang, source_hint=""
        )
        if stronger and stronger != out:
            nw = len(stronger.split())
            if nw >= int(orig_words * min_ret) and _is_complete_thought(stronger):
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
                    return out, reasons, False
    except Exception:
        pass

    # Keep leading *complete* sentences only.
    cand, _ = _keep_leading_complete_sentences(
        out, slot_ms, lang, min_words=max(4, int(orig_words * min_ret))
    )
    if cand != out:
        out = cand
        reasons.append("keep_leading_sentences")

    pred = estimate_tts_ms(out, lang)
    if pred > int(slot_ms * SEVERE_OVERFLOW_RATIO):
        cand2, _ = _keep_leading_complete_sentences(
            out, slot_ms, lang, min_words=4
        )
        if cand2 != out and _is_complete_thought(cand2):
            out = cand2
            reasons.append("severe_keep_leading")

    # Refuse hard char-budget cut (Stage 4 TZ) — mild overflow preferred.
    pred = estimate_tts_ms(out, lang)
    if pred > target and not _is_complete_thought(out):
        # Revert to last complete original sentences rather than broken tail.
        parts = _split_sentences(text)
        if parts:
            safe = parts[0]
            if _is_complete_thought(safe):
                out = safe
                reasons.append("reverted_incomplete_tail")
            else:
                meaning_truncated = True
                reasons.append("incomplete_refused")
                out = text  # keep full meaning; allow mild overflow
    return out, reasons, meaning_truncated


def _light_expand(text: str, slot_ms: int, lang: str) -> tuple[str, list[str]]:
    """Optional mild expand — no LLM required; skip if nothing safe."""
    t = str(text or "").strip()
    if not t or len(t.split()) > 8:
        return t, []
    pred = estimate_tts_ms(t, lang)
    if pred >= slot_ms * UNDERFILL_EXPAND_RATIO:
        return t, []
    return t, []


def fit_text_to_slot(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    source_hint: str = "",
    allow_expand: bool = True,
) -> TextFitResult:
    """One Happy Path step: paraphrase length toward slot without chopping speech."""
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

    hi = int(slot * OVERFLOW_FIT_RATIO)
    reasons: list[str] = []
    out = original
    action = "unchanged"
    meaning_truncated = False

    if before > hi:
        out, reasons, meaning_truncated = _safe_shorten(original, slot, lang)
        action = "shorten" if out != original else "unchanged"
    elif allow_expand and before < int(slot * UNDERFILL_EXPAND_RATIO):
        out, reasons = _light_expand(original, slot, lang)
        action = "expand" if out != original else "unchanged"
    else:
        action = "unchanged"

    after = estimate_tts_ms(out, lang)
    if not out.strip():
        out = original
        after = before
        action = "unchanged"
        reasons = ["reverted_empty"]
        meaning_truncated = False
    elif action == "shorten" and after > before and after > hi:
        out = original
        after = before
        action = "unchanged"
        reasons = ["reverted_worse"]
        meaning_truncated = False
    elif out != original and not _is_complete_thought(out):
        # Never ship a mid-thought fragment to TTS / Review.
        out = original
        after = before
        action = "unchanged"
        reasons = list(reasons) + ["reverted_incomplete"]
        meaning_truncated = False

    result = TextFitResult(
        text=out,
        slot_ms=slot,
        predicted_ms_before=before,
        predicted_ms_after=after,
        action=action,
        changed=out != original,
        reasons=reasons,
        meaning_truncated=bool(meaning_truncated),
    )
    if result.changed or result.meaning_truncated:
        logger.info(
            "fit_text_to_slot: action=%s slot=%d pred %d→%d truncated=%s reasons=%s",
            action,
            slot,
            before,
            after,
            result.meaning_truncated,
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
