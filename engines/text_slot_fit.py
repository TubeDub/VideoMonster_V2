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
# Stage 17/19: expand / atempo-slow when predicted < slot * 0.90 — kill dead air.
UNDERFILL_EXPAND_RATIO = 0.90
# Soft expand aim — leave a little natural room under the slot.
EXPAND_AIM_RATIO = 0.95
# After fit, still under this → mark atempo_slow (audio path must stretch).
UNDERFILL_ATEMPO_SLOW_RATIO = 0.88
# Stage 19i: production atempo band — expand/split first; never "fast then gap".
ATEMPO_MIN = 0.90
ATEMPO_MAX = 1.20
MAX_ATEMPO_SLOW = ATEMPO_MIN
MAX_ATEMPO_FAST = ATEMPO_MAX
FORBIDDEN_FAST_THEN_GAP = True
FAST_GAP_ATEMPO_THRESHOLD = 1.05
# Stage 15: refuse shorten that drops >15% of words (severe floor 30%).
MIN_WORD_RETENTION = 0.85
MIN_WORD_RETENTION_SEVERE = 0.70
SEVERE_OVERFLOW_RATIO = 1.50
# Stage 19d: final shorter than raw/semantic by >25% without shorten_executed = silent truncate.
MAX_SILENT_TRUNCATE_RATIO = 0.75
MIN_RAW_WORDS_FOR_TRUNCATE_GUARD = 12
# Stage 19i: Ukrainian CPS budget (production typical).
TARGET_CPS_UK = 14.0
MIN_CPS_UK = 11.5
MAX_CPS_UK = 17.0
# Stage 19e–19i: after restore, force split when predicted speech exceeds slot.
FORCE_SPLIT_RATIO = 1.15
FORCE_SPLIT_ABS_MS = 3000
MAX_POST_RESTORE_SPLIT_CHILDREN = 10
MAX_CHILD_FILL = 1.15
MAX_FILL_RATIO_AFTER_RESTORE = 1.15
MAX_SPLIT_CHILDREN = 10
MAX_SPLIT_DEPTH = 3
MIN_CHILD_SLOT_MS = 2200
MAX_CHILD_SLOT_MS = 7500
STAGE19H_OK_FILL_LO = 0.85
STAGE19H_OK_FILL_HI = 1.15
STAGE19I_OK_FILL_LO = 0.85
STAGE19I_OK_FILL_HI = 1.15
STAGE19J_OK_FILL_LO = 0.85
STAGE19J_OK_FILL_HI = 1.15
UNDERFLOW_TRIGGER_MS = 350
OVERFLOW_TRIGGER_MS = 350
MAX_SOFT_PADS_PER_SEG = 1
# Stage 19i/19j: soft expand pads (strict whitelist — never "ось як це було тоді").
SOFT_PAD_WHITELIST: tuple[str, ...] = (
    "саме тоді",
    "і саме в цей момент",
    "отже",
    "тому",
)
_STAGE19G_SOFT_PADS = SOFT_PAD_WHITELIST
_STAGE19H_SOFT_PADS = SOFT_PAD_WHITELIST
_STAGE19I_SOFT_PADS = SOFT_PAD_WHITELIST
_STAGE19J_SOFT_PADS = SOFT_PAD_WHITELIST
# Stage 19j: garbage expand tails — ", Джордж." / ", Вісімнадцятирічний."
FORBIDDEN_EXPAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r",\s*\S+\s*[.!?…]?\s*$"),
    re.compile(r"(?i),\s*Вісімнадцятирічний\b"),
    re.compile(r"(?i),\s*Джордж\s*[.!?…]?\s*$"),
    re.compile(r"(?i),\s*займався\s*[.!?…]?\s*$"),
    re.compile(r"(?i),\s*\w{3,}\s*[.!?…]\s*$"),
)
_CLAUSE_CONJ = (
    "і", "та", "але", "а", "що", "коли", "тому", "тоді", "бо", "адже",
    "якщо", "хоча", "який", "яка", "які", "де", "куди",
)
_STAGE19G_GLOSSARY_ANCHORS: tuple[tuple[str, str], ...] = (
    (r"(?i)\bgeorge\s+junior\b|\bgeorge\s+jr\.?\b", "Джордж молодший"),
    (r"(?i)\bfiat\b", "Фіат"),
    (r"(?i)\busc\b", "USC"),
    (r"(?i)\bhaskell\s+wexler\b", "Хаскелл Векслер"),
    (r"(?i)\bwexler\b", "Векслер"),
    (r"(?i)\bstar\s+wars\b", "Зоряні війни"),
)

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


def semantic_anchor_text(seg: dict[str, Any] | None = None, *, fallback: str = "") -> str:
    """Best meaning-complete UA source for expand / anti-truncate (Stage 19d)."""
    if not isinstance(seg, dict):
        return " ".join(str(fallback or "").split()).strip()
    for key in (
        "raw_translation",
        "raw_mt",
        "semantic_engine_text",
        "mt_text",
        "approved_text",
    ):
        t = " ".join(str(seg.get(key) or "").split()).strip()
        if t:
            return t
    return " ".join(str(fallback or "").split()).strip()


def detect_silent_truncate(
    final_text: str,
    raw_or_semantic: str,
    *,
    shorten_executed: bool = False,
    max_ratio: float = MAX_SILENT_TRUNCATE_RATIO,
    min_raw_words: int = MIN_RAW_WORDS_FOR_TRUNCATE_GUARD,
) -> bool:
    """True when Final lost >25% words vs raw/semantic without an explicit shorten."""
    if shorten_executed:
        return False
    raw = " ".join(str(raw_or_semantic or "").split()).strip()
    final = " ".join(str(final_text or "").split()).strip()
    if not raw or not final:
        return False
    rw = len(raw.split())
    fw = len(final.split())
    if rw < int(min_raw_words):
        return False
    return fw < rw * float(max_ratio)


def safe_shorten(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    source_hint: str = "",
) -> tuple[str, list[str], bool]:
    """Public Stage 19d wrapper — whole-thought shorten, retention ≥ 0.85."""
    return _safe_shorten(text, slot_ms, lang, source_hint=source_hint)


def clean_text_chars(text: str) -> str:
    """Non-space characters used for CPS / char-budget checks."""
    return str(text or "").replace(" ", "")


def char_budget(slot_ms: int, cps: float = TARGET_CPS_UK) -> int:
    """Stage 19i: max comfortable characters for a slot at target CPS."""
    return max(20, int((max(0, int(slot_ms or 0)) / 1000.0) * float(cps or TARGET_CPS_UK)))


def estimated_cps(text: str, measured_ms: int) -> float:
    """Stage 19i: characters/sec from clean text and measured audio."""
    ms = int(measured_ms or 0)
    if ms <= 0:
        return 0.0
    return len(clean_text_chars(text)) / (ms / 1000.0)


def soft_pad_count(text: str) -> int:
    """Count whitelist soft pads present in text (Stage 19i ≤1)."""
    low = str(text or "").lower()
    n = 0
    for pad in SOFT_PAD_WHITELIST:
        if pad.lower() in low:
            n += 1
    return n


def cps_over_budget(text: str, slot_ms: int, *, ratio: float = 1.15) -> bool:
    """True when clean text exceeds char_budget × ratio → shorten/split first."""
    budget = char_budget(slot_ms)
    return len(clean_text_chars(text)) > int(budget * float(ratio))


def cps_under_budget(text: str, slot_ms: int, *, ratio: float = 0.85) -> bool:
    """True when clean text is below char_budget × ratio → expand."""
    budget = char_budget(slot_ms)
    return len(clean_text_chars(text)) < int(budget * float(ratio))


def production_child_aim_ms(slot_ms: int = 0, *, predicted_ms: int = 0) -> int:
    """Target pack aim in [MIN_CHILD_SLOT_MS, MAX_CHILD_SLOT_MS].

    Honors the caller's pack slot when inside the band; caps large parent
    windows to MAX_CHILD_SLOT_MS so monologues become 3–7.5s chunks.
    ``predicted_ms`` is reserved for callers that pre-size aim.
    """
    del predicted_ms  # API compatibility — aim follows slot, not raw pred.
    slot = max(0, int(slot_ms or 0))
    if slot <= 0:
        return MAX_CHILD_SLOT_MS
    return max(MIN_CHILD_SLOT_MS, min(MAX_CHILD_SLOT_MS, slot))


def should_force_split(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    predicted_ms: int | None = None,
) -> bool:
    """Stage 19i: predicted fill > 1.15 or CPS over char_budget."""
    slot = max(0, int(slot_ms or 0))
    pred = int(predicted_ms) if predicted_ms is not None else estimate_tts_ms(text, lang)
    if pred <= 0:
        return False
    if slot > 0 and cps_over_budget(text, slot):
        return True
    if slot <= 0:
        return pred > FORCE_SPLIT_ABS_MS
    if pred > int(slot * FORCE_SPLIT_RATIO):
        return True
    # Tiny slots: absolute 3s+ speech that still overshoots the slot hard.
    return slot < FORCE_SPLIT_ABS_MS and pred > FORCE_SPLIT_ABS_MS and pred > slot


def has_forbidden_expand_pattern(text: str) -> bool:
    """Stage 19j: True when text has garbage append (e.g. ', Джордж.')."""
    t = _normalize_chunk(text)
    if not t:
        return False
    for pat in FORBIDDEN_EXPAND_PATTERNS:
        if pat.search(t):
            return True
    return False


def is_clean_utterance(text: str) -> bool:
    """Stage 19j: finished sentence or clean clause — never a word crumb."""
    t = _normalize_chunk(text)
    if not t or len(t.split()) < 3:
        return False
    if t.endswith(",") or t.endswith(";") or t.endswith(":"):
        return False
    if has_forbidden_expand_pattern(t):
        return False
    # Reject lone "Word." / "Word," crumbs.
    if re.fullmatch(r"\S+[.,!?;…]?", t):
        return False
    if not _is_complete_thought(t):
        return False
    if _COMPLETE_END.search(t):
        return True
    # Clean subordinate / clause without final punct (≥4 words, no dangling conj).
    if len(t.split()) >= 4 and not _BAD_TAIL.search(t):
        return True
    return False


def _split_strong_sentences(text: str) -> list[str]:
    """Split ONLY on . ! ? … and strong semicolons."""
    t = _normalize_chunk(text)
    if not t:
        return []
    parts = re.split(r"(?<=[.!?…;])\s+", t)
    return [p.strip() for p in parts if p.strip()]


def _split_clauses(text: str) -> list[str]:
    """Split a long sentence on commas before conjunctions (і, але, що…)."""
    t = _normalize_chunk(text)
    if not t:
        return []
    conj = "|".join(re.escape(c) for c in _CLAUSE_CONJ)
    # ", і …" / ", але …" — keep conjunction with the right clause.
    parts = re.split(rf"(?<=,)\s+(?=(?:{conj})\b)", t, flags=re.IGNORECASE)
    cleaned = [_normalize_chunk(p) for p in parts if _normalize_chunk(p)]
    if len(cleaned) < 2:
        # Fallback: split on " і " / " але " mid-clause (no comma).
        parts2 = re.split(
            rf"\s+(?=\b(?:{'|'.join(re.escape(c) for c in ('і', 'та', 'але', 'а'))})\b)",
            t,
            flags=re.IGNORECASE,
        )
        cleaned = [_normalize_chunk(p) for p in parts2 if _normalize_chunk(p)]
    return cleaned if len(cleaned) >= 2 else [t]


def _ensure_chunk_terminal(text: str) -> str:
    """Ensure a clean chunk ends with sentence punctuation when appropriate."""
    t = _normalize_chunk(text)
    if not t:
        return t
    # Never leave a dangling comma / colon tail from clause cuts.
    t = t.rstrip(" \t,;:")
    if not t:
        return t
    if _COMPLETE_END.search(t) or t.endswith((";", "…")):
        return t
    # Clause starting with conjunction — still prefer a terminal period.
    first = t.split()[0].lower().strip("«\"'")
    if first in _CLAUSE_CONJ and len(t.split()) >= 4:
        return t + "."
    if len(t.split()) >= 3:
        return t + "."
    return t


def _atomic_text_parts(text: str) -> list[str]:
    """Stage 19j: sentence units only — never word crumbs."""
    t = _normalize_chunk(text)
    if not t:
        return []
    parts = _split_strong_sentences(t)
    if len(parts) < 2:
        parts = _split_clauses(t)
    return [p for p in parts if p] or ([t] if t else [])


def _filter_clean_unique_chunks(parent: str, chunks: list[str]) -> list[str]:
    """Keep unique clean utterances; merge unclean crumbs into neighbors."""
    parent_n = _normalize_chunk(parent)
    raw = [_normalize_chunk(c) for c in chunks if _normalize_chunk(c)]
    if not raw:
        return []
    # Merge unclean / tiny into previous (or next if first).
    merged: list[str] = []
    pending = ""
    for c in raw:
        piece = (pending + " " + c).strip() if pending else c
        pending = ""
        if is_clean_utterance(piece) or (
            len(piece.split()) >= 4 and not has_forbidden_expand_pattern(piece)
        ):
            merged.append(_ensure_chunk_terminal(piece))
        elif merged:
            merged[-1] = _ensure_chunk_terminal((merged[-1] + " " + piece).strip())
        else:
            pending = piece
    if pending:
        if merged:
            merged[-1] = _ensure_chunk_terminal((merged[-1] + " " + pending).strip())
        else:
            merged.append(_ensure_chunk_terminal(pending))

    # Drop parent equals / dups / affix clones.
    out: list[str] = []
    for c in merged:
        cn = _normalize_chunk(c)
        if not cn or cn == parent_n:
            continue
        if any(cn == x for x in out):
            continue
        # Significant prefix/suffix of another chunk → skip the shorter clone.
        skip = False
        for x in list(out):
            if cn == x:
                skip = True
                break
            shorter, longer = (cn, x) if len(cn) <= len(x) else (x, cn)
            if len(shorter) >= 24 and (
                longer.startswith(shorter) or longer.endswith(shorter)
            ):
                if cn == shorter:
                    skip = True
                    break
                out = [y for y in out if y != shorter]
        if not skip:
            out.append(cn)
    # Final cleanliness gate.
    return [c for c in out if is_clean_utterance(c) or (
        len(c.split()) >= 4 and not has_forbidden_expand_pattern(c)
    )]


def split_into_slot_sized_chunks(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    max_children: int = MAX_POST_RESTORE_SPLIT_CHILDREN,
) -> list[str]:
    """Split long Final into ≤max_children packs aimed at ≈slot×0.95."""
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return []
    parts = _atomic_text_parts(t)
    if len(parts) < 2:
        return [t]

    # Aim near EXPAND_AIM; hard-cap at fill 1.25 so children stay in band.
    aim = max(400, int(max(1, slot_ms) * EXPAND_AIM_RATIO))
    hard = max(aim, int(max(1, slot_ms) * MAX_CHILD_FILL))
    packed: list[str] = []
    buf: list[str] = []
    for p in parts:
        trial = " ".join(buf + [p]).strip()
        pred = estimate_tts_ms(trial, lang)
        if buf and pred > hard and len(packed) < max_children - 1:
            packed.append(" ".join(buf).strip())
            buf = [p]
        else:
            buf.append(p)
    if buf:
        packed.append(" ".join(buf).strip())

    # If still too many, merge into max_children by weight.
    if len(packed) > max_children:
        weights = [max(1, len(c)) for c in packed]
        total = sum(weights) or 1
        target = total / float(max_children)
        merged: list[str] = []
        cur: list[str] = []
        wcur = 0
        for c, w in zip(packed, weights):
            if cur and (wcur + w) > target and len(merged) < max_children - 1:
                merged.append(" ".join(cur).strip())
                cur, wcur = [c], w
            else:
                cur.append(c)
                wcur += w
        if cur:
            merged.append(" ".join(cur).strip())
        packed = [x for x in merged if x]

    # Ensure at least 2 when force-split was warranted.
    if len(packed) == 1 and len(parts) >= 2:
        mid = max(1, len(parts) // 2)
        packed = [
            " ".join(parts[:mid]).strip(),
            " ".join(parts[mid:]).strip(),
        ]
    return [p for p in packed if p][: max(1, int(max_children))]


def _normalize_chunk(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def assert_unique_split_chunks(
    parent_text: str,
    chunks: list[str],
) -> bool:
    """Stage 19h: True when every child chunk is a unique proper substring ≠ parent."""
    parent = _normalize_chunk(parent_text)
    cleaned = [_normalize_chunk(c) for c in chunks if _normalize_chunk(c)]
    if len(cleaned) < 2:
        return False
    seen: set[str] = set()
    for c in cleaned:
        if c == parent:
            return False
        if c in seen:
            return False
        seen.add(c)
    return True


def assert_clean_split_chunks(chunks: list[str]) -> bool:
    """Stage 19j: every child is a clean utterance."""
    cleaned = [_normalize_chunk(c) for c in chunks if _normalize_chunk(c)]
    if len(cleaned) < 2:
        return False
    return all(is_clean_utterance(c) for c in cleaned)


def force_split_until_fit(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    max_children: int = MAX_SPLIT_CHILDREN,
    depth: int = 0,
) -> list[str]:
    """Stage 19j: unique clean sentence/clause chunks (no word crumbs).

    1) Split on . ! ? ;
    2) Oversized sentence → clauses (comma + conjunctions)
    3) Pack to ~2.2–7.5s; recurse while fill > 1.15
    Never mid-word / mid-phrase garbage packs.
    """
    t = _normalize_chunk(text)
    if not t:
        return []
    parent_slot = max(1, int(slot_ms or 0) or MIN_CHILD_SLOT_MS)
    parent_pred = estimate_tts_ms(t, lang)
    slot = production_child_aim_ms(parent_slot, predicted_ms=parent_pred)
    hard = max(int(slot * MAX_CHILD_FILL), MIN_CHILD_SLOT_MS)
    budget_chars = int(char_budget(slot) * 1.15)

    def _sentence_units(src: str) -> list[str]:
        units = _split_strong_sentences(src)
        if len(units) < 2:
            units = _split_clauses(src)
        return [u for u in units if u] or ([src] if src else [])

    def _pack_units(units: list[str]) -> list[str]:
        if not units:
            return []
        if len(units) == 1:
            return list(units)
        packed: list[str] = []
        buf: list[str] = []
        for u in units:
            trial = " ".join(buf + [u]).strip()
            pred = estimate_tts_ms(trial, lang)
            if buf and pred > hard and len(packed) < max_children - 1:
                packed.append(" ".join(buf).strip())
                buf = [u]
            else:
                buf.append(u)
        if buf:
            packed.append(" ".join(buf).strip())
        return packed[: max(1, int(max_children))]

    if int(depth or 0) >= MAX_SPLIT_DEPTH:
        units = _sentence_units(t)
        if len(units) >= 2:
            mid = max(1, len(units) // 2)
            left = _ensure_chunk_terminal(" ".join(units[:mid]))
            right = _ensure_chunk_terminal(" ".join(units[mid:]))
            out = _filter_clean_unique_chunks(t, [left, right])
            return out if out else [t]
        return [t]

    units = _sentence_units(t)
    # Oversized single sentence → clause split before packing.
    expanded: list[str] = []
    for u in units:
        pred = estimate_tts_ms(u, lang)
        if pred > hard or len(clean_text_chars(u)) > budget_chars:
            clauses = _split_clauses(u)
            if len(clauses) >= 2:
                expanded.extend(clauses)
            else:
                expanded.append(u)
        else:
            expanded.append(u)

    chunks = _pack_units(expanded)
    if len(chunks) < 2 and len(expanded) >= 2:
        mid = max(1, len(expanded) // 2)
        chunks = [
            " ".join(expanded[:mid]).strip(),
            " ".join(expanded[mid:]).strip(),
        ]

    result: list[str] = []
    for c in chunks:
        c = _normalize_chunk(c)
        if not c or len(result) >= max_children:
            break
        if c == t and max_children >= 2:
            sub_units = _sentence_units(c)
            if len(sub_units) >= 2:
                mid = max(1, len(sub_units) // 2)
                left = _ensure_chunk_terminal(" ".join(sub_units[:mid]))
                right = _ensure_chunk_terminal(" ".join(sub_units[mid:]))
                if left != t:
                    result.append(left)
                if right != t and len(result) < max_children:
                    result.append(right)
                continue
        pred = estimate_tts_ms(c, lang)
        room = max_children - len(result)
        over = pred > hard or len(clean_text_chars(c)) > budget_chars
        if over and room >= 2 and int(depth or 0) + 1 <= MAX_SPLIT_DEPTH:
            sub = force_split_until_fit(
                c,
                production_child_aim_ms(
                    max(MIN_CHILD_SLOT_MS, int(slot * 0.85)),
                    predicted_ms=pred,
                ),
                lang,
                max_children=min(max_children, room),
                depth=int(depth or 0) + 1,
            )
            if len(sub) >= 2 and assert_unique_split_chunks(c, sub):
                for s in sub:
                    sn = _normalize_chunk(s)
                    if sn and sn != t and len(result) < max_children:
                        result.append(sn)
                continue
            # Clause bipartition only — never word mid-split.
            clauses = _split_clauses(c)
            if len(clauses) >= 2:
                mid = max(1, len(clauses) // 2)
                left = _ensure_chunk_terminal(" ".join(clauses[:mid]))
                right = _ensure_chunk_terminal(" ".join(clauses[mid:]))
                if left and left != t:
                    result.append(left)
                if right and right != t and len(result) < max_children:
                    result.append(right)
                continue
        if c != t or not result:
            result.append(_ensure_chunk_terminal(c))

    out = _filter_clean_unique_chunks(t, result)[:max_children]
    if len(out) < 2 and should_force_split(t, slot, lang):
        units2 = _sentence_units(t)
        if len(units2) >= 2:
            # One sentence per child when possible (up to max_children).
            packs = units2[:max_children]
            if len(units2) > max_children:
                packs = _pack_units(units2)
            out = _filter_clean_unique_chunks(t, packs)[:max_children]

    if len(out) >= 2 and assert_unique_split_chunks(t, out):
        return out
    if len(out) >= 2:
        return out
    return [t] if t else []


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
    action: str = "none"  # ok|shorten|expand|unchanged|atempo_prefer|atempo_slow|expand_then_slow|dead_air_risk
    changed: bool = False
    reasons: list[str] = field(default_factory=list)
    meaning_truncated: bool = False
    meaning_preserved: bool = True
    dead_air_risk_ms: int = 0
    fill_ratio: float = 0.0
    atempo: float = 1.0
    strategy: str = "ok"
    predicted_tts_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def suggested_atempo_for_fill(predicted_ms: int, slot_ms: int) -> float:
    """Map fill need to atempo in [MAX_ATEMPO_SLOW, MAX_ATEMPO_FAST]."""
    slot = max(1, int(slot_ms or 0))
    pred = max(1, int(predicted_ms or 0))
    fill = pred / float(slot)
    if fill >= UNDERFILL_EXPAND_RATIO:
        # Mild speed-up only when overfilling toward OVERFLOW band.
        if pred > int(slot * OVERFLOW_FIT_RATIO):
            need = pred / float(slot)
            return min(MAX_ATEMPO_FAST, max(1.0, need))
        return 1.0
    # Underfill → slow down (lengthen audio): atempo = pred/slot.
    tempo = pred / float(slot)
    return max(MAX_ATEMPO_SLOW, min(1.0, tempo))


def forbid_fast_then_gap(atempo: float, fill_ratio: float) -> bool:
    """True when strategy would compress speech and leave a dead-air tail."""
    if not FORBIDDEN_FAST_THEN_GAP:
        return False
    return float(atempo) > FAST_GAP_ATEMPO_THRESHOLD and float(fill_ratio) < UNDERFILL_EXPAND_RATIO


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

    # Light pacing / full-form inserts that do not invent facts (Stage 19).
    if lang0 == "uk":
        # Negative lookahead — never stack the same soft insert (Тож тоді тоді…).
        inserts = (
            (r"\bТож\b(?!\s+тоді)", "Тож тоді"),
            (r"\bОтже\b(?!\s+тоді)", "Отже тоді"),
            (r"\bАле\b(?!\s+при\s+цьому)", "Але при цьому"),
            (r"\bІ\b(?!\s+також)", "І також"),
            (r"\bПотім\b(?!\s+згодом)", "Потім згодом"),
            (r"(?<!Саме\s)\bТому\b", "Саме тому"),
            (r"(?<!Саме\s)\bКоли\b", "Саме коли"),
        )
        restoratives = (
            (" був ", " справді був "),
            (" була ", " справді була "),
            (" були ", " справді були "),
            (" став ", " тоді став "),
            (" стала ", " тоді стала "),
            (" стали ", " тоді стали "),
            (" вирішив", " зрештою вирішив"),
            (" вирішила", " зрештою вирішила"),
            (" подав ", " тоді подав "),
            (" пішов ", " далі пішов "),
            (" пішла ", " далі пішла "),
            (" зробив ", " тоді зробив "),
            (" зробила ", " тоді зробила "),
            (" сказав ", " тоді сказав "),
            (" сказала ", " тоді сказала "),
            (" купив ", " тоді купив "),
            (" вижив ", " усе ж вижив "),
            (" працював ", " тоді працював "),
        )
        # Safe synonym expansions (no new claims).
        synonyms = (
            (" авто ", " автомобіль "),
            (" машину ", " автомобіль "),
            (" машини ", " автомобіля "),
            (" фото ", " фотоапарат "),
            (" кіно ", " кінематограф "),
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
        synonyms = ()
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
        synonyms = ()

    # Prefer one restorative substitution first (fact-neutral intensifier).
    for a, b in restoratives:
        if a in cand and b not in cand:
            trial = cand.replace(a, b, 1)
            if _is_complete_thought(trial) or _COMPLETE_END.search(trial):
                return trial

    for a, b in synonyms:
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


def _expand_keeps_entities(original: str, expanded: str, source_hint: str = "") -> bool:
    """Refuse expand that drops critical entities (Star Wars, Wexler, Fiat, …)."""
    return not _critical_markers_lost(original, expanded, source_hint)


def _expand_lang_ok(text: str, lang: str) -> bool:
    lang0 = str(lang or "uk").split("-")[0].lower()
    if lang0 != "uk":
        return True
    try:
        from engines.tts_lang_lock import is_uk_tts_text_ok

        return is_uk_tts_text_ok(text)
    except Exception:
        return True


def expand_text_to_slot(
    text: str,
    slot_ms: int,
    lang: str = "uk",
    *,
    source_hint: str = "",
    raw_mt: str = "",
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

    original = out
    # Retention vs UK Final/Raw — never use EN source_hint as word-count denominator.
    meaning_anchor = " ".join(str(raw_mt or original).split()).strip() or original

    # Stage 19c/19d: if Raw/semantic is longer and meaning-safe, prefer it over short Final.
    raw_full = " ".join(str(raw_mt or "").split()).strip()
    if (
        raw_full
        and raw_full != out
        and len(raw_full.split()) > len(out.split())
        and _is_complete_thought(raw_full)
        and _expand_lang_ok(raw_full, lang)
        and _expand_keeps_entities(original, raw_full, source_hint)
        # Prefer raw when it restores markers present in source (out may be truncated).
        and not _critical_markers_lost(
            f"{original} {source_hint}".strip(), raw_full, source_hint
        )
    ):
        raw_pred = estimate_tts_ms(raw_full, lang)
        # Prefer raw when underfilled; if raw mildly overflows, safe_shorten toward aim.
        if pred < floor:
            if raw_pred <= int(slot * OVERFLOW_FIT_RATIO * 1.08):
                out = strip_slot_pad_fillers(raw_full)
                pred = estimate_tts_ms(out, lang)
                reasons.append("raw_prefer")
                if pred >= floor:
                    return out, reasons
            elif raw_pred > int(slot * OVERFLOW_FIT_RATIO):
                shortened, sh_reasons, _tr = _safe_shorten(
                    raw_full, slot, lang, source_hint=source_hint
                )
                if (
                    shortened
                    and shortened != out
                    and word_retention_ratio(raw_full, shortened) >= MIN_WORD_RETENTION
                    and estimate_tts_ms(shortened, lang) > pred
                ):
                    out = strip_slot_pad_fillers(shortened)
                    pred = estimate_tts_ms(out, lang)
                    reasons.append("raw_prefer")
                    reasons.extend(list(sh_reasons or []) + ["safe_shorten_raw"])
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
            and _expand_lang_ok(llm_out, lang)
            and _expand_keeps_entities(original, llm_out, source_hint)
            and word_retention_ratio(meaning_anchor, llm_out) >= MIN_WORD_RETENTION
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

    # Rule-based passes until near aim or no progress (Stage 19c: up to 8).
    for _ in range(8):
        if estimate_tts_ms(out, lang) >= aim:
            break
        nxt = _rule_expand_once(out, lang, source_hint=source_hint)
        if not nxt or nxt == out:
            break
        if not (_is_complete_thought(nxt) or _COMPLETE_END.search(nxt)):
            break
        nxt = strip_slot_pad_fillers(nxt)
        if not _expand_lang_ok(nxt, lang):
            break
        if not _expand_keeps_entities(original, nxt, source_hint):
            break
        # Retention vs meaning anchor: expand may add words, never drop core.
        if word_retention_ratio(meaning_anchor, nxt) < MIN_WORD_RETENTION:
            # Allow longer-than-anchor expands (retention > 1.0 is fine).
            if len(nxt.split()) < int(len(meaning_anchor.split()) * MIN_WORD_RETENTION):
                break
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


def _stage19g_inject_glossary_anchors(
    text: str,
    *,
    source_hint: str = "",
    raw_mt: str = "",
) -> tuple[str, bool]:
    """Insert missing glossary entities (George Jr., Fiat, USC, Wexler, Star Wars)."""
    out = " ".join(str(text or "").split()).strip()
    if not out:
        return out, False
    probe = f"{source_hint} {raw_mt} {out}"
    changed = False
    for pat, uk_form in _STAGE19G_GLOSSARY_ANCHORS:
        if not re.search(pat, probe):
            continue
        if uk_form.lower() in out.lower():
            continue
        # Append once as a natural clause — meaning-bearing, not empty pad.
        trial = f"{out.rstrip('.!?…')} — {uk_form}."
        trial = strip_slot_pad_fillers(" ".join(trial.split()).strip())
        if (
            trial
            and trial != out
            and len(trial.split()) > len(out.split())
            and _expand_lang_ok(trial, "uk")
        ):
            out = trial
            changed = True
            break
    return out, changed


def _stage19g_soft_pad_once(text: str, lang: str = "uk") -> tuple[str, bool]:
    """Insert at most one whitelist soft pad (Stage 19i ≤ MAX_SOFT_PADS_PER_SEG)."""
    out = " ".join(str(text or "").split()).strip()
    if not out:
        return out, False
    lang0 = str(lang or "uk").split("-")[0].lower()
    if lang0 != "uk":
        return out, False
    if soft_pad_count(out) >= MAX_SOFT_PADS_PER_SEG:
        return out, False
    for pad in SOFT_PAD_WHITELIST:
        if pad.lower() in out.lower():
            continue
        # Prefer mid-clause insert after first comma / first clause.
        if "," in out:
            left, right = out.split(",", 1)
            trial = f"{left.strip()}, {pad},{right}"
        else:
            trial = f"{pad.capitalize()}, {out[0].lower() + out[1:]}" if out[0].isupper() else f"{pad}, {out}"
        trial = strip_slot_pad_fillers(" ".join(trial.split()).strip())
        if (
            trial
            and trial != out
            and len(clean_text_chars(trial)) > len(clean_text_chars(out))
            and soft_pad_count(trial) <= MAX_SOFT_PADS_PER_SEG
            and _is_complete_thought(trial)
            and _expand_lang_ok(trial, lang)
        ):
            return trial, True
    return out, False


def _stage19j_repeat_key_phrase(text: str) -> tuple[str, bool]:
    """Repeat a multi-word key entity as a full clean sentence — never ', Word.'."""
    out = _normalize_chunk(text)
    if not out or len(out.split()) < 3:
        return out, False
    # Prefer known multi-word glossary forms already present / injectible.
    phrases = (
        "Джордж молодший",
        "Хаскелл Векслер",
        "Зоряні війни",
        "старий Фіат",
    )
    entity = ""
    low = out.lower()
    for ph in phrases:
        if ph.lower() in low:
            entity = ph
            break
    if not entity:
        # Bigram of capitalized / long content words (never a single stem dump).
        words = out.split()
        stop = {
            "і", "та", "а", "але", "що", "як", "в", "у", "на", "з", "із", "до",
            "від", "про", "для", "це", "той", "він", "вона", "вони", "був", "була",
            "були", "є", "не", "тоді", "потім", "саме", "отже", "тому",
        }
        for i in range(len(words) - 1):
            a = re.sub(r"[^\w\u0400-\u04FF]+", "", words[i], flags=re.UNICODE)
            b = re.sub(r"[^\w\u0400-\u04FF]+", "", words[i + 1], flags=re.UNICODE)
            if (
                len(a) >= 4
                and len(b) >= 4
                and a.lower() not in stop
                and b.lower() not in stop
            ):
                entity = f"{a} {b}"
                break
    if not entity:
        return out, False
    base = out if _COMPLETE_END.search(out) else out.rstrip(",;:") + "."
    trial = f"{base} Саме про {entity} тут ідеться."
    trial = strip_slot_pad_fillers(_normalize_chunk(trial))
    if (
        trial != out
        and len(clean_text_chars(trial)) > len(clean_text_chars(out))
        and is_clean_utterance(trial)
        and not has_forbidden_expand_pattern(trial)
    ):
        return trial, True
    return out, False


def _stage19g_repeat_key_noun(text: str) -> tuple[str, bool]:
    """Deprecated Stage 19g path — routed to clean phrase repeat (19j)."""
    return _stage19j_repeat_key_phrase(text)


def expand_to_fill(
    final_text: str,
    *,
    target_ms: int,
    lang: str = "uk",
    source_hint: str = "",
    raw_mt: str = "",
    prefer_raw: str = "",
    target_chars: int | None = None,
    strategy_order: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, list[str]]:
    """Stage 19j: lengthen only with clean utterances — never garbage appends.

    Order: semantic_repeat_key_phrase → glossary_expand → soft_pad_once (≤1).
    If no clean growth possible → return original (caller marks dead_air_risk).
    """
    before = _normalize_chunk(final_text)
    before_chars = len(clean_text_chars(before))
    slot_equiv = int(max(0, target_ms) / max(EXPAND_AIM_RATIO, 0.01))
    aim_chars = int(target_chars) if target_chars is not None else char_budget(slot_equiv)
    anchor = _normalize_chunk(prefer_raw or raw_mt)
    reasons: list[str] = []
    blocked = 0
    aim = max(200, int(target_ms or 0))
    order = list(
        strategy_order
        or ("semantic_repeat_key", "glossary_expand", "soft_pad_once")
    )

    def _accept(candidate: str) -> bool:
        c = _normalize_chunk(candidate)
        if not c or c == before:
            return False
        if len(clean_text_chars(c)) <= before_chars:
            return False
        if has_forbidden_expand_pattern(c):
            return False
        if not is_clean_utterance(c) and not (
            _is_complete_thought(c) and not has_forbidden_expand_pattern(c)
        ):
            return False
        if not _expand_lang_ok(c, lang):
            return False
        return True

    def _still_short(candidate: str) -> bool:
        c = _normalize_chunk(candidate)
        return (
            len(clean_text_chars(c)) < int(aim_chars * 0.85)
            or estimate_tts_ms(c, lang) < aim
        )

    out = before
    # Rule expand first — accept only if clean.
    try:
        ruled, rule_reasons = expand_text_to_slot(
            before,
            slot_equiv,
            lang,
            source_hint=source_hint,
            raw_mt=anchor or raw_mt,
        )
        ruled = _normalize_chunk(ruled) or before
        if _accept(ruled):
            out = ruled
            reasons.extend(list(rule_reasons or []))
        elif ruled != before:
            blocked += 1
            reasons.append("stage19j:garbage_expand_blocked")
    except Exception:
        pass

    # Longer clean semantic/raw replacement (whole text, not a crumb append).
    if (
        (_still_short(out) or out == before)
        and anchor
        and anchor != out
        and _accept(anchor)
    ):
        out = strip_slot_pad_fillers(anchor)
        reasons.append("stage19j:force_raw_prefer")
        if "raw_prefer" not in reasons:
            reasons.append("raw_prefer")

    for step in order:
        if _accept(out) and not _still_short(out) and out != before:
            break
        if step in ("semantic_repeat_key", "semantic_repeat_key_phrase"):
            rep, ok = _stage19j_repeat_key_phrase(out)
            if ok and _accept(rep):
                out = rep
                reasons.append("stage19j:semantic_repeat_key")
            elif ok:
                blocked += 1
                reasons.append("stage19j:garbage_expand_blocked")
        elif step == "glossary_expand":
            inj, ok = _stage19g_inject_glossary_anchors(
                out, source_hint=source_hint, raw_mt=anchor or raw_mt
            )
            if ok and _accept(inj):
                out = _normalize_chunk(inj)
                reasons.append("stage19j:glossary_expand")
            elif ok:
                blocked += 1
                reasons.append("stage19j:garbage_expand_blocked")
        elif step == "soft_pad_once":
            if soft_pad_count(out) >= MAX_SOFT_PADS_PER_SEG:
                continue
            padded, ok = _stage19g_soft_pad_once(out, lang)
            if ok and _accept(padded) and soft_pad_count(padded) <= MAX_SOFT_PADS_PER_SEG:
                out = padded
                reasons.append("stage19j:soft_pad")
            elif ok:
                blocked += 1
                reasons.append("stage19j:garbage_expand_blocked")

    out = strip_slot_pad_fillers(_normalize_chunk(out))
    # Hard refuse: any forbidden pattern or no real clean growth.
    if has_forbidden_expand_pattern(out) or not _accept(out):
        if out != before:
            blocked += 1
            reasons.append("stage19j:garbage_expand_blocked")
        out = before
        reasons = [r for r in reasons if "grown" not in str(r)]
        if blocked:
            reasons.append(f"stage19j:blocked_count:{blocked}")
        return out, reasons
    reasons.append("stage19j:text_grown")
    if blocked:
        reasons.append(f"stage19j:blocked_count:{blocked}")
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
    raw_mt: str = "",
) -> TextFitResult:
    """Stage 19: paraphrase length toward slot — expand first, then mild atempo."""
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
            fill_ratio=0.0,
            atempo=1.0,
            strategy="ok",
            predicted_tts_ms=before,
        )

    hi = int(slot * OVERFLOW_FIT_RATIO)
    lo = int(slot * UNDERFILL_EXPAND_RATIO)
    reasons: list[str] = []
    out = original
    action = "unchanged"
    meaning_truncated = False
    meaning_preserved = True
    did_expand = False

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
        # Stage 5/19: if shorten overshot into dead air, expand back toward the band.
        after_short = estimate_tts_ms(out, lang)
        if (
            allow_expand
            and action == "shorten"
            and after_short < lo
            and out.strip()
            and not meaning_truncated
        ):
            expanded, er = expand_to_fill(
                out,
                target_ms=int(slot * EXPAND_AIM_RATIO),
                lang=lang,
                source_hint=source_hint,
                raw_mt=raw_mt or original,
            )
            if expanded and expanded != out:
                out = expanded
                did_expand = True
                reasons = list(reasons) + er + ["expand_after_shorten"]
                action = "expand" if estimate_tts_ms(out, lang) >= lo else "shorten"
    elif allow_expand and before < lo:
        # Stage 19 priority: expand text first (not atempo).
        out, reasons = expand_to_fill(
            original,
            target_ms=int(slot * EXPAND_AIM_RATIO),
            lang=lang,
            source_hint=source_hint,
            raw_mt=raw_mt or original,
        )
        if out != original:
            did_expand = True
            action = "expand"
        else:
            action = "unchanged"
            reasons = list(reasons) + ["expand_noop"]
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
        did_expand = False
    elif action == "shorten" and after > before and after > hi:
        out = original
        after = before
        action = "atempo_prefer"
        reasons = list(reasons) + ["reverted_worse"]
        meaning_truncated = False
        meaning_preserved = True
    elif out != original and (
        not _is_complete_thought(out) or _BAD_TAIL.search(out)
    ):
        # Never ship a mid-thought / truncated fragment to TTS / Review.
        out = original
        after = before
        action = "atempo_prefer"
        reasons = list(reasons) + ["reverted_incomplete"]
        meaning_truncated = False
        meaning_preserved = True
        did_expand = False
    elif (
        out != original
        and len(out.split()) < len(original.split())
        and word_retention_ratio(original, out) < MIN_WORD_RETENTION
    ):
        # Shorten lost meaning — expand is allowed to grow past original.
        out = original
        after = before
        action = "atempo_prefer"
        reasons = list(reasons) + ["reverted_retention"]
        meaning_truncated = False
        meaning_preserved = True
        did_expand = False
        logger.info(
            "fit_text_to_slot: meaning_preserved=True refuse shorten slot=%d",
            slot,
        )

    # Stage 17/19: still underfilled after expand → atempo_slow (not fast+gap).
    slow_floor = int(slot * UNDERFILL_ATEMPO_SLOW_RATIO)
    if after < slow_floor and action not in ("atempo_prefer",):
        if did_expand:
            action = "expand_then_slow"
            reasons = list(reasons) + ["atempo_slow_after_expand"]
        elif action == "unchanged" or after < lo:
            action = "atempo_slow"
            reasons = list(reasons) + ["atempo_slow_underfill"]

    fill_ratio = float(after) / float(max(slot, 1))
    atempo = suggested_atempo_for_fill(after, slot)

    # Stage 19-C: forbid atempo>1.05 with fill<0.90 — roll back to slow/expand.
    if forbid_fast_then_gap(atempo, fill_ratio):
        reasons = list(reasons) + ["expand_or_slow_not_fast_gap"]
        atempo = suggested_atempo_for_fill(after, slot)
        if atempo > FAST_GAP_ATEMPO_THRESHOLD:
            atempo = 1.0
        if fill_ratio < UNDERFILL_EXPAND_RATIO:
            atempo = max(MAX_ATEMPO_SLOW, min(1.0, fill_ratio))
            if did_expand:
                action = "expand_then_slow"
            else:
                action = "atempo_slow"
        logger.info(
            "fit_text_to_slot: forbid fast+gap → atempo=%.3f fill=%.2f action=%s",
            atempo,
            fill_ratio,
            action,
        )

    # Clamp atempo into Stage 19 band.
    atempo = max(MAX_ATEMPO_SLOW, min(MAX_ATEMPO_FAST, float(atempo)))
    dead_air_risk_ms = max(0, slot - after)
    strategy = action if action not in ("none", "unchanged") else "ok"
    if after < lo and action in ("unchanged", "none"):
        strategy = "dead_air_risk"
        action = "dead_air_risk"
        reasons = list(reasons) + ["dead_air_risk_underfill"]
    if dead_air_risk_ms > 350 and strategy == "ok":
        strategy = "dead_air_risk"

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
        fill_ratio=round(fill_ratio, 4),
        atempo=round(atempo, 4),
        strategy=strategy,
        predicted_tts_ms=int(after),
    )
    # Soft assert: underfill must be handled by expand / atempo_slow / prefer.
    if after < slow_floor and result.action not in (
        "expand",
        "atempo_slow",
        "expand_then_slow",
        "atempo_prefer",
        "dead_air_risk",
    ):
        result.action = "atempo_slow"
        result.strategy = "atempo_slow"
        result.reasons = list(result.reasons) + ["assert_atempo_slow"]
        result.atempo = round(
            max(MAX_ATEMPO_SLOW, min(1.0, result.fill_ratio)), 4
        )
    if (
        result.changed
        or result.meaning_truncated
        or result.action
        in ("atempo_prefer", "atempo_slow", "expand", "expand_then_slow", "dead_air_risk")
        or dead_air_risk_ms > 350
    ):
        logger.info(
            "fit_text_to_slot: action=%s strategy=%s slot=%d pred %d→%d "
            "fill=%.2f atempo=%.3f dead_air_risk_ms=%d reasons=%s",
            result.action,
            result.strategy,
            slot,
            before,
            after,
            result.fill_ratio,
            result.atempo,
            dead_air_risk_ms,
            result.reasons,
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
