"""Repetition guard — remove duplicated sentences / clause runs before TTS.

TZ §8: detect and remove repeated sentences, paragraphs, and meaning blocks
introduced by MT/naturalizer BEFORE the text reaches TTS. Conservative by
design: it only drops verbatim or near-verbatim consecutive repeats, never
rephrases or removes unique content (so meaning is preserved, TZ §4/§6).
"""

from __future__ import annotations

import re
import unicodedata

# Sentence boundary: keep the terminal punctuation with the sentence.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
# Minimum repeated word-run length to treat as an accidental duplicate.
_MIN_RUN_WORDS = 3


def _normalize(text: str) -> str:
    """Lowercase, drop combining stress marks, strip punctuation, collapse spaces."""
    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s, flags=re.UNICODE)
    return " ".join(s.split())


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(str(text or "").strip())
    return [p for p in (s.strip() for s in parts) if p]


def has_repetition(text: str) -> bool:
    """True if the text contains a repeated sentence or repeated word-run."""
    _, changed = remove_repeated_sentences(text)
    return changed


def _dedupe_sentences(sentences: list[str]) -> tuple[list[str], bool]:
    kept: list[str] = []
    kept_norm: list[str] = []
    changed = False
    for sent in sentences:
        norm = _normalize(sent)
        if not norm:
            kept.append(sent)
            continue
        drop = False
        for j, prev in enumerate(kept_norm):
            if not prev:
                continue
            if norm == prev:
                drop = True
                break
            # Current is a truncated repeat of an already-kept sentence.
            if len(norm) >= 8 and (prev.startswith(norm) or norm.startswith(prev)):
                # Keep the longer (more complete) of the two.
                if len(norm) > len(prev):
                    kept[j] = sent
                    kept_norm[j] = norm
                drop = True
                break
        if drop:
            changed = True
            continue
        kept.append(sent)
        kept_norm.append(norm)
    return kept, changed


def _collapse_repeated_word_runs(text: str) -> tuple[str, bool]:
    """Collapse an immediately-repeated run of >= _MIN_RUN_WORDS words.

    Example: "він робив поворот він робив поворот" → "він робив поворот".
    Comparison ignores stress marks / case so TTS stress diacritics don't hide
    a duplicate.
    """
    words = str(text or "").split()
    n = len(words)
    if n < _MIN_RUN_WORDS * 2:
        return text, False
    norm_words = [_normalize(w) for w in words]
    changed = False
    i = 0
    out: list[str] = []
    while i < n:
        collapsed = False
        # Try the longest run first, down to the minimum length.
        max_run = (n - i) // 2
        for run in range(max_run, _MIN_RUN_WORDS - 1, -1):
            a = norm_words[i : i + run]
            b = norm_words[i + run : i + 2 * run]
            if a == b and all(a):
                out.extend(words[i : i + run])
                i += 2 * run
                changed = True
                collapsed = True
                break
        if not collapsed:
            out.append(words[i])
            i += 1
    return " ".join(out), changed


def remove_repeated_sentences(text: str) -> tuple[str, bool]:
    """Remove duplicated sentences and immediately-repeated word runs.

    Returns (cleaned_text, changed). Pure cleanup — never rephrases.
    """
    raw = str(text or "").strip()
    if not raw:
        return raw, False

    sentences = _split_sentences(raw)
    cleaned_sents, sent_changed = _dedupe_sentences(sentences)
    joined = " ".join(cleaned_sents) if cleaned_sents else raw

    collapsed, run_changed = _collapse_repeated_word_runs(joined)
    collapsed = " ".join(collapsed.split())
    return collapsed, bool(sent_changed or run_changed)


def dedupe_segment_texts(texts: list[str]) -> tuple[list[str], list[int]]:
    """Apply repetition removal to each segment; return (cleaned, changed_indices)."""
    out: list[str] = []
    changed_idx: list[int] = []
    for i, t in enumerate(texts):
        cleaned, changed = remove_repeated_sentences(t)
        out.append(cleaned)
        if changed:
            changed_idx.append(i)
    return out, changed_idx


def token_overlap_ratio(a: str, b: str) -> float:
    """Share of tokens in ``a`` that also appear in ``b`` (and vice-versa, max)."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return max(inter / len(ta), inter / len(tb))


def is_near_repeat(current: str, previous: str, *, threshold: float = 0.80) -> bool:
    """True when current ≈ previous at ≥80% token overlap."""
    cur = str(current or "").strip()
    prev = str(previous or "").strip()
    if not cur or not prev:
        return False
    if _normalize(cur) == _normalize(prev):
        return True
    return token_overlap_ratio(cur, prev) >= float(threshold)


def strip_looped_clauses(text: str, *, min_repeats: int = 3) -> tuple[str, bool]:
    """Cut a clause that repeats 3+ times inside one segment.

    ``він сказав так. він сказав так. він сказав так.`` → one clause.
    """
    raw = str(text or "").strip()
    if not raw:
        return raw, False
    sentences = _split_sentences(raw)
    if len(sentences) >= min_repeats:
        norms = [_normalize(s) for s in sentences]
        first = norms[0]
        if first and all(n == first for n in norms) and len(norms) >= min_repeats:
            return sentences[0], True
        # Consecutive run of the same clause
        out: list[str] = []
        changed = False
        i = 0
        while i < len(sentences):
            run = 1
            while (
                i + run < len(sentences)
                and norms[i]
                and norms[i + run] == norms[i]
            ):
                run += 1
            out.append(sentences[i])
            if run >= min_repeats:
                changed = True
            elif run > 1:
                # keep one of a 2x run (same as sentence dedupe)
                changed = True
            i += run
        if changed:
            return " ".join(out), True

    collapsed, run_changed = _collapse_repeated_word_runs(raw)
    # Extra pass: same 3+ word clause glued without punctuation.
    words = collapsed.split()
    n = len(words)
    if n >= 3 * 3:
        norm_w = [_normalize(w) for w in words]
        for run in range(min(12, n // min_repeats), 2, -1):
            chunk = norm_w[:run]
            if not all(chunk):
                continue
            times = 1
            pos = run
            while pos + run <= n and norm_w[pos : pos + run] == chunk:
                times += 1
                pos += run
            if times >= min_repeats and pos == n:
                return " ".join(words[:run]), True
    return collapsed, run_changed


def _rule_rephrase_away_from(current: str, previous: str) -> str:
    """Offline shorten/rephrase so TTS text is not a near-copy of the neighbor."""
    cur = " ".join(str(current or "").split()).strip()
    if not cur:
        return cur
    cleaned, _ = strip_looped_clauses(cur)
    cleaned, _ = remove_repeated_sentences(cleaned)
    if cleaned and not is_near_repeat(cleaned, previous):
        return cleaned
    words = cleaned.split() or cur.split()
    # Drop a trailing restatement half when the line is long enough.
    if len(words) >= 8:
        half = words[: max(4, (len(words) * 2) // 3)]
        trial = " ".join(half)
        if trial and not is_near_repeat(trial, previous):
            return trial
    # Last resort: keep current but strip loop — never leave empty.
    return cleaned or cur


def block_or_rewrite_repeat(
    current: str,
    previous: str,
    *,
    raw_mt: str | None = None,
    semantic: str | None = None,
    threshold: float = 0.80,
) -> tuple[str, bool]:
    """If current ≈ previous (≥80% overlap) take raw_mt/semantic or rule-rephrase."""
    cur = str(current or "").strip()
    looped, loop_changed = strip_looped_clauses(cur)
    cur = looped
    if not is_near_repeat(cur, previous, threshold=threshold):
        return cur, loop_changed
    for alt in (raw_mt, semantic):
        cand = str(alt or "").strip()
        if not cand:
            continue
        cand, _ = strip_looped_clauses(cand)
        cand, _ = remove_repeated_sentences(cand)
        if cand and not is_near_repeat(cand, previous, threshold=threshold):
            return cand, True
    rewritten = _rule_rephrase_away_from(cur, previous)
    if rewritten != str(current or "").strip():
        return rewritten, True
    return rewritten, True


def rewrite_near_repeats(
    texts: list[str],
    *,
    raw_mts: list[str] | None = None,
    semantics: list[str] | None = None,
) -> tuple[list[str], int]:
    """Rewrite lines that overlap the previous spoken line by ≥80% tokens."""
    out: list[str] = []
    blocked = 0
    prev = ""
    for i, t in enumerate(texts or []):
        raw = raw_mts[i] if raw_mts and i < len(raw_mts) else None
        sem = semantics[i] if semantics and i < len(semantics) else None
        was_repeat = is_near_repeat(str(t or ""), prev)
        new, _changed = block_or_rewrite_repeat(t, prev, raw_mt=raw, semantic=sem)
        if was_repeat and new != str(t or "").strip():
            blocked += 1
        out.append(new)
        if new:
            prev = new
    return out, blocked


def repair_unresolved_segment_text(
    seg: dict,
    *,
    previous: str = "",
) -> tuple[str, bool]:
    """Rule text attempt before closed_loop soft-complete (no LLM required)."""
    cur = str(
        (seg or {}).get("final_tts_text")
        or (seg or {}).get("plain_text")
        or (seg or {}).get("text")
        or ""
    ).strip()
    raw_mt = str(
        (seg or {}).get("raw_mt")
        or (seg or {}).get("raw_translation")
        or (seg or {}).get("semantic_engine_text")
        or ""
    ).strip()
    try:
        from engines.text_slot_fit import prepare_uk_spoken_text, strip_slot_pad_fillers

        cur = prepare_uk_spoken_text(cur)
        if raw_mt:
            raw_mt = strip_slot_pad_fillers(raw_mt)
    except Exception:
        pass
    new, changed = block_or_rewrite_repeat(cur, previous, raw_mt=raw_mt or None)
    if not new:
        new = raw_mt or cur
        changed = bool(new)
    if new and new != str(
        (seg or {}).get("final_tts_text")
        or (seg or {}).get("text")
        or ""
    ).strip():
        seg["final_tts_text"] = new
        seg["plain_text"] = new
        seg["text"] = new
        changed = True
    return new, changed


def dedupe_adjacent_copies(texts: list[str]) -> list[str]:
    """Blank adjacent segments that repeat the previous spoken line (Stage 37)."""
    out: list[str] = []
    prev_norm = ""
    for t in texts:
        cur = str(t or "").strip()
        try:
            from engines.text_slot_fit import strip_slot_pad_fillers

            core = _normalize(strip_slot_pad_fillers(cur))
        except Exception:
            core = _normalize(cur)
        if core and prev_norm and (core == prev_norm or core in prev_norm or prev_norm in core):
            # Same utterance (or pad-stripped clone) as the previous line.
            if len(core) >= 8 or core == prev_norm:
                out.append("")
                continue
        out.append(cur)
        if core:
            prev_norm = core
    return out


# Stage 42 — neighbor TTS bleed (partial overlap, not only full clones).
TTS_BLEED_TOKEN_THRESHOLD = 0.30
_BLEED_STOP = frozenset(
    {
        "я",
        "ти",
        "він",
        "вона",
        "це",
        "так",
        "і",
        "й",
        "в",
        "у",
        "на",
        "з",
        "що",
        "не",
        "як",
        "а",
        "але",
        "чи",
        "вже",
        "тут",
        "там",
        "мені",
        "тебе",
        "його",
        "її",
        "ми",
        "ви",
        "о",
        "ну",
        "то",
        "той",
        "ця",
        "the",
        "a",
        "an",
        "to",
        "of",
        "and",
        "i",
        "you",
        "it",
    }
)


def _content_tokens(text: str) -> list[str]:
    return [w for w in _normalize(text).split() if w and w not in _BLEED_STOP and len(w) > 1]


def neighbor_bleed_ratio(current: str, neighbor: str) -> float:
    """How much of ``current`` is neighbor text (and vice-versa swallow)."""
    cur = str(current or "").strip()
    nb = str(neighbor or "").strip()
    if not cur or not nb:
        return 0.0
    nc, nn = _normalize(cur), _normalize(nb)
    if not nc or not nn:
        return 0.0
    if nc == nn:
        return 1.0
    if nn in nc or nc in nn:
        ta = _content_tokens(cur)
        tb = _content_tokens(nb)
        if not ta or not tb:
            return 1.0
        inter = len(set(ta) & set(tb))
        return max(inter / len(set(ta)), inter / len(set(tb)), 0.99)
    ta = set(_content_tokens(cur))
    tb = set(_content_tokens(nb))
    if not ta or not tb:
        return token_overlap_ratio(cur, nb)
    inter = ta & tb
    copy_r = len(inter) / len(ta)
    swallow_r = len(inter) / len(tb)
    return max(copy_r, swallow_r)


def _has_content_ngram(current: str, neighbor: str, *, n: int = 3) -> bool:
    """True when a 3-token (or 2 if neighbor is short) span of neighbor is in current."""
    ca = _content_tokens(current)
    cb = _content_tokens(neighbor)
    if len(cb) < 2 or len(ca) < 2:
        return False
    size = n if len(cb) >= n else 2
    if len(cb) < size:
        return False
    for i in range(len(cb) - size + 1):
        span = cb[i : i + size]
        for j in range(len(ca) - size + 1):
            if ca[j : j + size] == span:
                return True
    return False


def is_tts_neighbor_bleed(
    current: str,
    neighbor: str,
    *,
    threshold: float = TTS_BLEED_TOKEN_THRESHOLD,
) -> bool:
    """True when current TTS text swallowed a neighbor replica (partial or full)."""
    cur = str(current or "").strip()
    nb = str(neighbor or "").strip()
    if not cur or not nb:
        return False
    nc, nn = _normalize(cur), _normalize(nb)
    if not nc or not nn:
        return False
    if nc == nn:
        return True
    # Current contains the neighbor (N TTS includes N+1 / N-1).
    if nn in nc and len(nc) > len(nn):
        if len(nn.split()) >= 2 or len(_content_tokens(nb)) >= 2:
            return True
    if _has_content_ngram(cur, nb, n=3):
        return True
    ta = set(_content_tokens(cur))
    tb = set(_content_tokens(nb))
    if not ta or not tb:
        return False
    copy_r = len(ta & tb) / len(ta)
    # Bag-of-words 30% only when a sequential span was copied (not shared slang).
    if copy_r > float(threshold) and _has_content_ngram(cur, nb, n=3):
        return True
    return False


def _strip_neighbor_span(current: str, neighbor: str) -> str:
    cur = " ".join(str(current or "").split()).strip()
    nb = " ".join(str(neighbor or "").split()).strip()
    if not cur or not nb:
        return cur
    if nb and nb in cur:
        out = cur.replace(nb, " ", 1)
        return " ".join(out.split()).strip(" ,;:—–-")
    # Sentence drop: drop sentences that overlap the neighbor heavily.
    sents = _split_sentences(cur)
    kept: list[str] = []
    for sent in sents:
        if is_tts_neighbor_bleed(sent, nb) and not is_tts_neighbor_bleed(sent, cur[: max(8, len(sent))]):
            continue
        if _normalize(sent) == _normalize(nb):
            continue
        kept.append(sent)
    if kept and " ".join(kept).strip() != cur:
        return " ".join(kept).strip()
    return cur


_UK_EN_CLAUSE_BRIDGES: tuple[tuple[str, str], ...] = (
    (r"їси чуже сміття|їси\s+чуже", r"eat\s+(?:other|.*trash)|trash.*feed"),
    (r"будь-який хлопець", r"any\s+guy|just\s+any"),
    (r"10 цент|десяти?\s+цент", r"ten cents|10 cents"),
    (r"ось долар|долар\s*\?", r"dollar"),
    (r"зелен", r"greens"),
    (r"голодн", r"hungry"),
    (r"нормальн\w*\s+тут", r"normal out here"),
    (r"цілих\s+два\s+дні", r"two whole days"),
    (r"прикрию", r"i got you"),
    (r"риюсь у смітник|перериваю смітник", r"dig through"),
)


def clause_explained_by_source(clause: str, source: str) -> bool:
    """True when an EN source actually owns this UK clause."""
    cl = str(clause or "").lower()
    src = str(source or "").lower()
    if not cl or not src:
        return False
    for uk_pat, en_pat in _UK_EN_CLAUSE_BRIDGES:
        if re.search(uk_pat, cl, re.I) and re.search(en_pat, src, re.I):
            return True
    return False


def _drop_borrowed_neighbor_clauses(
    current: str,
    neighbor: str,
    *,
    own_source: str = "",
    neighbor_source: str = "",
) -> str:
    """Drop a clause that is neighbor-owned, including comma-appended spans."""
    cur = " ".join(str(current or "").split()).strip()
    nb = " ".join(str(neighbor or "").split()).strip()
    if not cur or not nb:
        return cur
    # Distinctive 3-token spans copied from the neighbor.
    ca = _content_tokens(cur)
    cb = _content_tokens(nb)
    if len(cb) >= 3:
        for i in range(len(cb) - 2):
            span = cb[i : i + 3]
            span_s = " ".join(span)
            if span_s and span_s in _normalize(cur):
                own_ok = clause_explained_by_source(span_s, own_source)
                nb_ok = clause_explained_by_source(span_s, neighbor_source)
                if nb_ok and not own_ok:
                    # Strip the original-cased span loosely.
                    pat = r"\s*".join(re.escape(t) for t in span)
                    cur = re.sub(pat, " ", cur, count=1, flags=re.I)
                    cur = " ".join(cur.split()).strip(" ,;:—–-")
    sents = _split_sentences(cur)
    if len(sents) >= 2:
        kept: list[str] = []
        for sent in sents:
            if not is_tts_neighbor_bleed(sent, nb) and _normalize(sent) not in _normalize(nb):
                kept.append(sent)
                continue
            own_ok = clause_explained_by_source(sent, own_source)
            nb_ok = clause_explained_by_source(sent, neighbor_source)
            if nb_ok and not own_ok:
                continue
            if own_source and neighbor_source and nb_ok and own_ok:
                kept.append(sent)
                continue
            if own_ok:
                kept.append(sent)
                continue
            if own_source and not clause_explained_by_source(sent, own_source):
                if is_tts_neighbor_bleed(sent, nb) or _normalize(sent) in _normalize(nb):
                    continue
            kept.append(sent)
        if kept:
            cur = " ".join(kept).strip()
    return cur


def rebuild_tts_from_own_slot(
    current: str,
    neighbor: str,
    *,
    raw_mt: str = "",
    naturalized: str = "",
    source_text: str = "",
    neighbor_source: str = "",
) -> str:
    """Rebuild Final/TTS from THIS segment's raw/naturalized only."""
    def _own(cand: str) -> str:
        t = " ".join(str(cand or "").split()).strip()
        if not t:
            return ""
        t = _drop_borrowed_neighbor_clauses(
            t, neighbor, own_source=source_text, neighbor_source=neighbor_source
        )
        return t

    for cand in (raw_mt, naturalized):
        t = _own(cand)
        if t and not is_tts_neighbor_bleed(t, neighbor):
            return t
    stripped = _strip_neighbor_span(current, neighbor)
    stripped = _drop_borrowed_neighbor_clauses(
        stripped, neighbor, own_source=source_text, neighbor_source=neighbor_source
    )
    if stripped and not is_tts_neighbor_bleed(stripped, neighbor):
        return stripped
    if raw_mt and str(raw_mt).strip():
        own = _own(str(raw_mt))
        if own and not is_tts_neighbor_bleed(own, neighbor):
            return own
        stripped_raw = _strip_neighbor_span(own, neighbor)
        stripped_raw = _drop_borrowed_neighbor_clauses(
            stripped_raw, neighbor, own_source=source_text, neighbor_source=neighbor_source
        )
        if stripped_raw:
            return stripped_raw
    return stripped or " ".join(str(current or "").split()).strip()


def refuse_naturalized_inheritance(naturalized: str, previous: str, own_raw: str) -> str:
    """Naturalized #2 must not copy all of #1."""
    nat = " ".join(str(naturalized or "").split()).strip()
    prev = " ".join(str(previous or "").split()).strip()
    raw = " ".join(str(own_raw or "").split()).strip()
    if not nat or not prev:
        return nat
    if is_near_repeat(nat, prev) or (_normalize(prev) and _normalize(prev) in _normalize(nat)):
        if raw and not is_near_repeat(raw, prev) and _normalize(prev) not in _normalize(raw):
            return raw
        stripped = _strip_neighbor_span(nat, prev)
        if stripped and not is_near_repeat(stripped, prev):
            return stripped
        return raw or stripped or nat
    return nat


def sanitize_neighbor_bleed(
    texts: list[str],
    *,
    raw_mts: list[str] | None = None,
    naturalized: list[str] | None = None,
    sources: list[str] | None = None,
    threshold: float = TTS_BLEED_TOKEN_THRESHOLD,
) -> tuple[list[str], int]:
    """Drop neighbor tokens from each slot. Returns (texts, blocked_count)."""
    rows = [str(t or "").strip() for t in (texts or [])]
    blocked = 0
    n = len(rows)
    for i in range(n):
        cur = rows[i]
        if not cur:
            continue
        raw = raw_mts[i] if raw_mts and i < len(raw_mts) else ""
        nat = naturalized[i] if naturalized and i < len(naturalized) else ""
        src = sources[i] if sources and i < len(sources) else ""
        new = cur
        changed = False
        nb_pairs: list[tuple[str, str]] = []
        if i > 0:
            if rows[i - 1]:
                nb_pairs.append(
                    (
                        rows[i - 1],
                        str(sources[i - 1]) if sources and i - 1 < len(sources) else "",
                    )
                )
            if raw_mts and i - 1 < len(raw_mts) and raw_mts[i - 1]:
                nb_pairs.append((str(raw_mts[i - 1]), ""))
        if i + 1 < n:
            if rows[i + 1]:
                nb_pairs.append(
                    (
                        rows[i + 1],
                        str(sources[i + 1]) if sources and i + 1 < len(sources) else "",
                    )
                )
            if raw_mts and i + 1 < len(raw_mts) and raw_mts[i + 1]:
                nb_pairs.append((str(raw_mts[i + 1]), ""))
        for nb, nb_src in nb_pairs:
            borrowed = _drop_borrowed_neighbor_clauses(
                new, nb, own_source=str(src or ""), neighbor_source=nb_src
            )
            if borrowed != new:
                new = borrowed
                changed = True
            if is_tts_neighbor_bleed(new, nb, threshold=threshold):
                rebuilt = rebuild_tts_from_own_slot(
                    new,
                    nb,
                    raw_mt=str(raw or ""),
                    naturalized=str(nat or ""),
                    source_text=str(src or ""),
                    neighbor_source=nb_src,
                )
                if rebuilt != new:
                    new = rebuilt
                    changed = True
        if changed:
            rows[i] = new
            blocked += 1
    return rows, blocked


def has_gey_vocative(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:^|[.!?…]\s)Гей[,!\s]|\bГей,\s*(?:чувак|мій|швидко)",
            str(text or ""),
        )
    )


def uk_gender_form_status(text: str, *, gender: str = "male") -> str:
    t = str(text or "")
    if str(gender or "male").lower().startswith("f"):
        if re.search(r"\bЯ\s+(?:втратив|зробив|сказав|пішов)\b", t):
            return "mismatch"
        return "ok"
    if re.search(r"\bЯ\s+\w+ла\b", t):
        return "mismatch"
    return "ok"


def stamp_stage42_segment_flags(
    seg: dict,
    *,
    previous_text: str = "",
    next_text: str = "",
    gender: str = "male",
) -> dict:
    """Diagnostics only — does not rewrite live text."""
    if not isinstance(seg, dict):
        return {}
    spoken = str(
        seg.get("final_tts_text") or seg.get("text_for_tts") or seg.get("text") or ""
    ).strip()
    bleed = bool(
        (previous_text and is_tts_neighbor_bleed(spoken, previous_text))
        or (next_text and is_tts_neighbor_bleed(spoken, next_text))
    )
    gey = has_gey_vocative(spoken)
    ru = False
    try:
        from engines.tts_lang_lock import uk_text_has_russian_leak

        ru = bool(uk_text_has_russian_leak(spoken))
    except Exception:
        ru = bool(re.search(r"\b(?:и|ты|что|это|меня|хорошо)\b", spoken))
    flags = {
        "speaker_id": str(seg.get("speaker_id") or seg.get("speaker") or ""),
        "final_tts_text": spoken,
        "bleed_flag": bleed,
        "has_gey_vocative": gey,
        "has_ru_leak": ru,
        "gender_form": uk_gender_form_status(spoken, gender=gender),
    }
    seg["bleed_flag"] = bleed
    seg["has_gey_vocative"] = gey
    seg["has_ru_leak"] = ru
    seg["gender_form"] = flags["gender_form"]
    return flags
