# -*- coding: utf-8 -*-
"""Happy Path text↔slot fit — paraphrase length, keep natural speech speed.

Stage 15 priority (Simple): meaning completeness > timing fit > atempo.
atempo ≤ 1.15 preferred over chopping sentences from Raw MT.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.text_slot_fit")

# Target band: predicted TTS within ±15% of slot.
FIT_TOLERANCE = 0.15
# Stage 4/5: shorten when predicted > slot * 1.08
OVERFLOW_FIT_RATIO = 1.08
# Stage 17: expand / atempo-slow when predicted < slot * 0.90 — kill dead air.
UNDERFILL_EXPAND_RATIO = 0.90
# Soft expand aim — leave a little natural room under the slot.
EXPAND_AIM_RATIO = 0.95
# After fit, still under this → mark atempo_slow (audio path must stretch).
UNDERFILL_ATEMPO_SLOW_RATIO = 0.88
# Stage 15: refuse shorten that drops >15% of words (severe floor 30%).
MIN_WORD_RETENTION = 0.85
MIN_WORD_RETENTION_SEVERE = 0.70
SEVERE_OVERFLOW_RATIO = 1.50

# Incomplete thought endings that must NEVER be voiced as a final cut.
_BAD_TAIL = re.compile(
    r"(?i)(?:\bй\s+застосувати|\bта\s+застосувати|\bі\s+застосувати|"
    r"\bнезважаючи\s+на\s+те|\bнесмотря\s+на\s+(?:это|то)|"
    r"\bdespite\s+(?:that|this)|\bin\s+spite\s+of\s+that|"
    r"\bвирішив|\bрешил|\bdecided|\bbegan|\bstarted|"
    r"\bне\s+міг\s+не\s+відчуват\w*\s*$|"
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

# Stage 11/15: do not chop tails that carry job / crash / film-entity meaning.
_CRITICAL_TAIL_GUARD = re.compile(
    r"(?i)\b("
    r"job|get\s+in|star\s+wars|lucas|wexler|"
    r"робот[ауеи]?|роботі|роботою|"
    r"поступл\w*|прийнятт\w*|"
    r"зоряні|лукас|векслер|"
    r"аварі\w*|вилет\w*|вижив\w*|викину\w*|розби\w*|"
    r"потенціал\w*|гоночн\w*|фотоапарат\w*"
    r")\b"
)
_CRITICAL_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bjob\b"),
    re.compile(r"(?i)\bget\s+in\b"),
    re.compile(r"(?i)\bstar\s+wars\b"),
    re.compile(r"(?i)\blucas\b"),
    re.compile(r"(?i)\bwexler\b"),
    re.compile(r"(?i)\bробот"),
    re.compile(r"(?i)\bроботі"),
    re.compile(r"(?i)\bпоступл"),
    re.compile(r"(?i)\bзоряні"),
    re.compile(r"(?i)\bлукас"),
    re.compile(r"(?i)\bвекслер"),
    re.compile(r"(?i)\bаварі"),
    re.compile(r"(?i)\bвилет"),
    re.compile(r"(?i)\bвижив"),
    re.compile(r"(?i)\bвикину"),
    re.compile(r"(?i)\bрозби"),
    re.compile(r"(?i)\bпотенціал"),
    re.compile(r"(?i)\bгоночн"),
    re.compile(r"(?i)\bфотоапарат"),
)


def word_retention_ratio(original: str, candidate: str) -> float:
    ow = len(str(original or "").split())
    if ow <= 0:
        return 1.0
    return len(str(candidate or "").split()) / float(ow)


def prefer_full_meaning_text(
    candidate: str,
    raw_mt: str,
    *,
    min_retention: float = MIN_WORD_RETENTION,
    src_lang: str = "en",
    tgt_lang: str = "uk",
) -> tuple[str, bool]:
    """If candidate lost >15% words vs Raw MT — restore Raw (post-finalize)."""
    cand = " ".join(str(candidate or "").split()).strip()
    raw = " ".join(str(raw_mt or "").split()).strip()
    if not raw:
        return cand, False
    try:
        from engines.mt.glossary_en_uk import finalize_mt_text

        raw = finalize_mt_text(src_lang, tgt_lang, raw)
    except Exception:
        pass
    if not raw:
        return cand, False
    if not cand:
        return raw, True
    if word_retention_ratio(raw, cand) < float(min_retention):
        # Stage 18: never restore latin/raw garbage over already-good uk Final.
        try:
            from engines.tts_lang_lock import is_uk_tts_text_ok

            if str(tgt_lang or "").split("-")[0].lower() == "uk":
                if is_uk_tts_text_ok(cand) and not is_uk_tts_text_ok(raw):
                    return cand, False
        except Exception:
            pass
        logger.info(
            "prefer_full_meaning: restore raw words %d→%d (ret=%.2f)",
            len(cand.split()),
            len(raw.split()),
            word_retention_ratio(raw, cand),
        )
        return raw, True
    return cand, False


def _needs_critical_tail_guard(text: str, source_hint: str = "") -> bool:
    blob = f"{text} {source_hint}"
    return bool(_CRITICAL_TAIL_GUARD.search(blob))


def _critical_markers_lost(original: str, shortened: str, source_hint: str = "") -> bool:
    """True when shorten dropped a critical meaning marker present in original/hint."""
    if not _needs_critical_tail_guard(original, source_hint):
        return False
    probe_src = f"{original} {source_hint}"
    for pat in _CRITICAL_MARKER_PATTERNS:
        if pat.search(probe_src) and not pat.search(shortened):
            # EN markers may map to UK — allow UK siblings for a few cases.
            if pat.pattern.lower().find("job") >= 0 and re.search(
                r"(?i)\bробот", shortened
            ):
                continue
            if pat.pattern.lower().find("star") >= 0 and re.search(
                r"(?i)\bзоряні", shortened
            ):
                continue
            if pat.pattern.lower().find("lucas") >= 0 and re.search(
                r"(?i)\bлукас", shortened
            ):
                continue
            if pat.pattern.lower().find("wexler") >= 0 and re.search(
                r"(?i)\bвекслер", shortened
            ):
                continue
            if pat.pattern.lower().find("get\\s+in") >= 0 and re.search(
                r"(?i)\b(поступл|прийнятт)", shortened
            ):
                continue
            return True
    return False


@dataclass
class TextFitResult:
    text: str
    slot_ms: int
    predicted_ms_before: int
    predicted_ms_after: int
    action: str = "none"  # none|shorten|expand|unchanged|atempo_prefer|atempo_slow
    changed: bool = False
    reasons: list[str] = field(default_factory=list)
    meaning_truncated: bool = False
    meaning_preserved: bool = True
    dead_air_risk_ms: int = 0

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
    """Drop trailing full sentences until near slot; never leave a truncated clause.

    Stage 5: stay inside [0.80×slot, 1.08×slot] when possible — do not overshoot
    into dead-air underfill just to avoid a mild overflow.
    """
    parts = _split_sentences(text)
    if len(parts) < 2:
        return text, False
    target_hi = max(200, int(slot_ms * OVERFLOW_FIT_RATIO))
    target_lo = max(200, int(slot_ms * UNDERFILL_EXPAND_RATIO))
    kept: list[str] = []
    for p in parts:
        trial = " ".join(kept + [p]).strip()
        pred = estimate_tts_ms(trial, lang)
        if pred <= target_hi or not kept:
            kept.append(p)
            continue
        # Prefer mild overflow over underfill when the next sentence still fits
        # the hard overflow band loosely (≤ 1.15×) and current is under floor.
        cur_pred = estimate_tts_ms(" ".join(kept).strip(), lang) if kept else 0
        if cur_pred < target_lo and pred <= int(slot_ms * 1.15):
            kept.append(p)
        break
    cand = " ".join(kept).strip()
    # Drop a trailing incomplete sentence if keep stopped mid-thought.
    while kept and not _is_complete_thought(" ".join(kept).strip()):
        kept.pop()
        cand = " ".join(kept).strip()
    # Stage 15: never ship a shorten that drops below retention or bad-tails.
    if cand and cand != text:
        if word_retention_ratio(text, cand) < MIN_WORD_RETENTION:
            return text, False
        if _BAD_TAIL.search(cand) or not _is_complete_thought(cand):
            return text, False
    # If still underfilled and original fits the overflow cap, keep original.
    if cand and estimate_tts_ms(cand, lang) < target_lo:
        orig_pred = estimate_tts_ms(text, lang)
        if orig_pred <= target_hi and _is_complete_thought(text):
            return text, False
    if (
        cand
        and cand != text
        and len(cand.split()) >= min_words
        and _is_complete_thought(cand)
        and word_retention_ratio(text, cand) >= MIN_WORD_RETENTION
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


def _safe_shorten(
    text: str,
    slot_ms: int,
    lang: str,
    *,
    source_hint: str = "",
) -> tuple[str, list[str], bool]:
    """Shorten by paraphrase / full sentences only. Never hard-cut mid-thought.

    Returns (text, reasons, meaning_truncated). meaning_truncated stays False —
    hard-cuts are refused rather than applied.
    Stage 11: refuse shorten that drops job / Star Wars / Lucas / admission tails.
    """
    reasons: list[str] = []
    meaning_truncated = False
    out = " ".join(str(text or "").split()).strip()
    if not out:
        return out, reasons, False
    original = out
    hint = str(source_hint or "")

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
            and not _critical_markers_lost(original, compressed, hint)
        ):
            out = compressed
            reasons.append("soft_compress")
    except Exception:
        pass

    pred = estimate_tts_ms(out, lang)
    if pred <= target:
        return out, reasons, False

    cleaned = _drop_parentheticals(out)
    if (
        cleaned != out
        and cleaned
        and _is_complete_thought(cleaned)
        and not _critical_markers_lost(original, cleaned, hint)
    ):
        out = cleaned
        reasons.append("drop_parens")
        pred = estimate_tts_ms(out, lang)
        if pred <= target:
            return out, reasons, False

    trimmed = _drop_redundant_clauses(out, lang)
    if (
        trimmed
        and trimmed != out
        and _is_complete_thought(trimmed)
        and not _critical_markers_lost(original, trimmed, hint)
    ):
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
    if (
        para != out
        and _is_complete_thought(para)
        and not _critical_markers_lost(original, para, hint)
    ):
        out = para
        reasons.append("clause_paraphrase")
        pred = estimate_tts_ms(out, lang)
        if pred <= target:
            return out, reasons, False

    try:
        from engines.soft_sync import shorten_text_for_slot

        stronger = shorten_text_for_slot(
            out, slot_ms=slot_ms, lang=lang, source_hint=hint
        )
        if stronger and stronger != out:
            nw = len(stronger.split())
            if (
                nw >= int(orig_words * min_ret)
                and _is_complete_thought(stronger)
                and not _critical_markers_lost(original, stronger, hint)
            ):
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

    # Keep leading *complete* sentences only — but refuse if critical tail lost.
    cand, _ = _keep_leading_complete_sentences(
        out, slot_ms, lang, min_words=max(4, int(orig_words * min_ret))
    )
    if cand != out and not _critical_markers_lost(original, cand, hint):
        out = cand
        reasons.append("keep_leading_sentences")
    elif cand != out and _critical_markers_lost(original, cand, hint):
        reasons.append("shorten_refused_critical_tail")
        return original, reasons, False

    pred = estimate_tts_ms(out, lang)
    if pred > int(slot_ms * SEVERE_OVERFLOW_RATIO):
        cand2, _ = _keep_leading_complete_sentences(
            out, slot_ms, lang, min_words=4
        )
        if (
            cand2 != out
            and _is_complete_thought(cand2)
            and not _critical_markers_lost(original, cand2, hint)
        ):
            out = cand2
            reasons.append("severe_keep_leading")
        elif cand2 != out and _critical_markers_lost(original, cand2, hint):
            reasons.append("shorten_refused_critical_tail")
            return original, reasons, False

    # Refuse hard char-budget cut (Stage 4 TZ) — mild overflow preferred.
    pred = estimate_tts_ms(out, lang)
    if pred > target and not _is_complete_thought(out):
        parts = _split_sentences(text)
        if parts:
            safe = parts[0]
            if _is_complete_thought(safe) and not _critical_markers_lost(
                original, safe, hint
            ):
                out = safe
                reasons.append("reverted_incomplete_tail")
            else:
                meaning_truncated = True
                reasons.append("incomplete_refused")
                out = text  # keep full meaning; allow mild overflow

    # Final Stage 11/15 guards: critical markers, retention, complete thought.
    if out != original and _critical_markers_lost(original, out, hint):
        reasons.append("shorten_refused_critical_tail")
        return original, reasons, False
    if out != original and word_retention_ratio(original, out) < MIN_WORD_RETENTION:
        reasons.append("shorten_refused_retention")
        logger.info(
            "meaning_preserved=True refuse shorten ret=%.2f words %d→%d",
            word_retention_ratio(original, out),
            len(original.split()),
            len(out.split()),
        )
        return original, reasons, False
    if out != original and (
        _BAD_TAIL.search(out) or not _is_complete_thought(out)
    ):
        reasons.append("shorten_refused_incomplete")
        return original, reasons, False

    return out, reasons, meaning_truncated


def _rule_expand_once(text: str, lang: str, source_hint: str = "") -> str:
    """One soft expansion pass — restate / pace only, no invented facts."""
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return t
    lang0 = str(lang or "uk").split("-")[0].lower()
    # Unpack tight punctuation into spoken cadence.
    cand = re.sub(r"\s*;\s*", ". ", t)
    cand = re.sub(r"\s*—\s*", ", ", cand)
    cand = " ".join(cand.split()).strip()

    # Light pacing inserts that do not add claims.
    if lang0 == "uk":
        inserts = (
            (r"\bТож\b", "Тож тоді"),
            (r"\bОтже\b", "Отже тоді"),
            (r"\bАле\b", "Але при цьому"),
            (r"\bІ\b", "І також"),
        )
        restoratives = (
            (" був ", " справді був "),
            (" була ", " справді була "),
            (" стали ", " тоді стали "),
            (" вирішив", " зрештою вирішив"),
            (" подав ", " тоді подав "),
        )
    elif lang0 == "ru":
        inserts = (
            (r"\bИтак\b", "Итак тогда"),
            (r"\bНо\b", "Но при этом"),
            (r"\bИ\b", "И также"),
        )
        restoratives = (
            (" был ", " действительно был "),
            (" была ", " действительно была "),
            (" решил", " в итоге решил"),
        )
    else:
        inserts = (
            (r"\bSo\b", "So then"),
            (r"\bBut\b", "But then"),
            (r"\bAnd\b", "And also"),
        )
        restoratives = (
            (" was ", " really was "),
            (" decided", " eventually decided"),
        )

    # Prefer one restorative substitution first (fact-neutral intensifier).
    for a, b in restoratives:
        if a in cand and b not in cand:
            trial = cand.replace(a, b, 1)
            if _is_complete_thought(trial) or _COMPLETE_END.search(trial):
                return trial

    for pat, repl in inserts:
        trial = re.sub(pat, repl, cand, count=1)
        if trial != cand and (_is_complete_thought(trial) or _COMPLETE_END.search(trial)):
            return trial

    # Do NOT invent pacing filler ("ось як це було тоді" / "Саме так: …").
    # Those poisoned Review Final and looped under repeated expand (George Jr. RCA).
    # Underfill is resolved by mild intensifiers above and/or slot shrink in timing_fit.
    return cand


def strip_slot_pad_fillers(text: str) -> str:
    """Remove Stage-5 invented pacing pads from Review/TTS text."""
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return t
    # Repeated pacing clause (UK/RU/EN)
    for pat in (
        r"(?:,?\s*—\s*)?ось як це було тоді\.?",
        r"(?:,?\s*—\s*)?вот как это было тогда\.?",
        r"(?:,?\s*—\s*)?that's how it was then\.?",
        r"(?:,?\s*—\s*)?that is how it was then\.?",
    ):
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    # Echo restatements
    for pat in (
        r"\s*Саме так:\s*[^.!?…]+[.!?…]?",
        r"\s*Именно так:\s*[^.!?…]+[.!?…]?",
        r"\s*That is:\s*[^.!?…]+[.!?…]?",
    ):
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:]){2,}", r"\1", t)
    t = t.strip(" ,;—-")
    if t and t[-1] not in ".!?…":
        # Keep terminal punctuation if we stripped a trailing pad after a clause.
        if re.search(r"[.!?…]\s*$", str(text or "")):
            pass
    return " ".join(t.split()).strip()


def expand_text_to_slot(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    source_hint: str = "",
) -> tuple[str, list[str]]:
    """Grow underfilled speech toward the slot without inventing facts.

    Returns (text, reasons). Used before TTS on Happy Path / Simple.
    """
    reasons: list[str] = []
    out = strip_slot_pad_fillers(" ".join(str(text or "").split()).strip())
    slot = max(0, int(slot_ms or 0))
    if not out or slot <= 0:
        return out, reasons

    floor = max(200, int(slot * UNDERFILL_EXPAND_RATIO))
    aim = max(floor, int(slot * EXPAND_AIM_RATIO))
    pred = estimate_tts_ms(out, lang)
    if pred >= floor:
        return out, reasons

    # Optional LLM expand (same gate as soft_sync) — still meaning-safe.
    try:
        from engines.soft_sync import expand_text_for_slot

        llm_out = expand_text_for_slot(
            out, slot_ms=slot, lang=lang, source_hint=source_hint
        )
        llm_out = " ".join(str(llm_out or "").split()).strip()
        if (
            llm_out
            and llm_out != out
            and _is_complete_thought(llm_out)
            and estimate_tts_ms(llm_out, lang) > pred
            and estimate_tts_ms(llm_out, lang) <= int(slot * OVERFLOW_FIT_RATIO * 1.05)
        ):
            out = strip_slot_pad_fillers(llm_out)
            pred = estimate_tts_ms(out, lang)
            reasons.append("llm_expand")
            if pred >= floor:
                return strip_slot_pad_fillers(out), reasons
    except Exception:
        pass

    # Rule-based passes until near aim or no progress.
    for _ in range(4):
        if estimate_tts_ms(out, lang) >= aim:
            break
        nxt = _rule_expand_once(out, lang, source_hint=source_hint)
        if not nxt or nxt == out:
            break
        if not (_is_complete_thought(nxt) or _COMPLETE_END.search(nxt)):
            break
        nxt = strip_slot_pad_fillers(nxt)
        nxt_pred = estimate_tts_ms(nxt, lang)
        if nxt_pred <= pred:
            break
        if nxt_pred > int(slot * OVERFLOW_FIT_RATIO * 1.08):
            break
        out = nxt
        pred = nxt_pred
        if "rule_expand" not in reasons:
            reasons.append("rule_expand")

    cleaned = strip_slot_pad_fillers(out)
    if cleaned != out:
        reasons.append("strip_pad_fillers")
        out = cleaned
    return out, reasons


def _light_expand(
    text: str,
    slot_ms: int,
    lang: str,
    *,
    source_hint: str = "",
) -> tuple[str, list[str]]:
    """Backward-compatible wrapper — real expand (Stage 5)."""
    return expand_text_to_slot(text, slot_ms, lang, source_hint=source_hint)


def fit_text_to_slot(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    source_hint: str = "",
    allow_expand: bool = True,
) -> TextFitResult:
    """One Happy Path step: paraphrase length toward slot without chopping speech."""
    original = strip_slot_pad_fillers(" ".join(str(text or "").split()).strip())
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
    lo = int(slot * UNDERFILL_EXPAND_RATIO)
    reasons: list[str] = []
    out = original
    action = "unchanged"
    meaning_truncated = False
    meaning_preserved = True

    if before > hi:
        # Soft compress first; if still overflow and shorten would drop meaning → atempo.
        try:
            from engines.mt.tts_slot_compress import soft_compress_for_slot

            soft = soft_compress_for_slot(
                original, slot_ms=slot, target_lang=lang
            )
            soft = " ".join(str(soft or "").split()).strip()
            if (
                soft
                and soft != original
                and _is_complete_thought(soft)
                and word_retention_ratio(original, soft) >= MIN_WORD_RETENTION
                and not _critical_markers_lost(original, soft, source_hint)
            ):
                out = soft
                reasons.append("soft_compress")
        except Exception:
            pass
        after_soft = estimate_tts_ms(out, lang)
        if after_soft > hi:
            shortened, sh_reasons, meaning_truncated = _safe_shorten(
                out, slot, lang, source_hint=source_hint
            )
            reasons = list(reasons) + list(sh_reasons)
            ret = word_retention_ratio(original, shortened)
            if (
                shortened != original
                and ret >= MIN_WORD_RETENTION
                and _is_complete_thought(shortened)
                and not _BAD_TAIL.search(shortened)
            ):
                out = shortened
                action = "shorten"
            else:
                # Stage 15: prefer mild atempo over chopping sentences.
                out = original
                action = "atempo_prefer"
                meaning_preserved = True
                meaning_truncated = False
                reasons.append("atempo_prefer_retention")
                logger.info(
                    "fit_text_to_slot: meaning_preserved=True action=atempo_prefer "
                    "slot=%d pred=%d retention_would=%.2f",
                    slot,
                    before,
                    ret if shortened != original else 1.0,
                )
        else:
            action = "shorten" if out != original else "unchanged"
        # Stage 5: if shorten overshot into dead air, expand back toward the band.
        after_short = estimate_tts_ms(out, lang)
        if (
            allow_expand
            and action == "shorten"
            and after_short < lo
            and out.strip()
            and not meaning_truncated
        ):
            expanded, er = expand_text_to_slot(
                out, slot, lang, source_hint=source_hint
            )
            if expanded and expanded != out:
                out = expanded
                reasons = list(reasons) + er + ["expand_after_shorten"]
                action = "expand" if estimate_tts_ms(out, lang) >= lo else "shorten"
    elif allow_expand and before < lo:
        out, reasons = expand_text_to_slot(
            original, slot, lang, source_hint=source_hint
        )
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
        meaning_preserved = True
    elif action == "shorten" and after > before and after > hi:
        out = original
        after = before
        action = "atempo_prefer"
        reasons = list(reasons) + ["reverted_worse"]
        meaning_truncated = False
        meaning_preserved = True
    elif out != original and (
        not _is_complete_thought(out)
        or _BAD_TAIL.search(out)
        or word_retention_ratio(original, out) < MIN_WORD_RETENTION
    ):
        # Never ship a mid-thought / truncated fragment to TTS / Review.
        out = original
        after = before
        action = "atempo_prefer"
        reasons = list(reasons) + ["reverted_incomplete_or_retention"]
        meaning_truncated = False
        meaning_preserved = True
        logger.info(
            "fit_text_to_slot: meaning_preserved=True refuse shorten slot=%d",
            slot,
        )

    # Stage 17: if still underfilled after expand — mark atempo_slow (audio stretch).
    slow_floor = int(slot * UNDERFILL_ATEMPO_SLOW_RATIO)
    if after < slow_floor and action not in ("atempo_prefer",):
        if action == "unchanged" or after < lo:
            action = "atempo_slow"
            reasons = list(reasons) + ["atempo_slow_underfill"]
    dead_air_risk_ms = max(0, slot - after)

    result = TextFitResult(
        text=out,
        slot_ms=slot,
        predicted_ms_before=before,
        predicted_ms_after=after,
        action=action,
        changed=out != original,
        reasons=reasons,
        meaning_truncated=bool(meaning_truncated),
        meaning_preserved=bool(meaning_preserved),
        dead_air_risk_ms=int(dead_air_risk_ms),
    )
    # Soft assert: underfill must be handled by expand / atempo_slow / prefer.
    if after < slow_floor and action not in (
        "expand",
        "atempo_slow",
        "atempo_prefer",
    ):
        result.action = "atempo_slow"
        result.reasons = list(result.reasons) + ["assert_atempo_slow"]
        action = "atempo_slow"
    if (
        result.changed
        or result.meaning_truncated
        or action in ("atempo_prefer", "atempo_slow", "expand")
        or dead_air_risk_ms > 350
    ):
        logger.info(
            "fit_text_to_slot: action=%s slot=%d pred %d→%d truncated=%s "
            "preserved=%s dead_air_risk_ms=%d reasons=%s",
            action,
            slot,
            before,
            after,
            result.meaning_truncated,
            result.meaning_preserved,
            dead_air_risk_ms,
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
