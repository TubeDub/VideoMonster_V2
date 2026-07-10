"""Quality estimation for MT — production-style QE without reference at runtime."""

from __future__ import annotations

import re
from typing import Any

_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁіїєІЇЄ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


def _norm_tokens(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or "").lower())


def chr_f_score(reference: str, hypothesis: str, *, n: int = 6) -> float:
    """
    Character n-gram F-score (chrF-lite) for offline benchmark ranking.
    Used by production systems when gold references exist.
    """
    ref = str(reference or "").strip()
    hyp = str(hypothesis or "").strip()
    if not ref or not hyp:
        return 0.0

    def _char_ngrams(s: str) -> dict[str, int]:
        s = re.sub(r"\s+", " ", s.lower()).strip()
        counts: dict[str, int] = {}
        for size in range(1, n + 1):
            for i in range(len(s) - size + 1):
                g = s[i : i + size]
                counts[g] = counts.get(g, 0) + 1
        return counts

    ref_g = _char_ngrams(ref)
    hyp_g = _char_ngrams(hyp)
    if not ref_g or not hyp_g:
        return 0.0

    overlap = 0
    hyp_total = sum(hyp_g.values())
    ref_total = sum(ref_g.values())
    for g, hc in hyp_g.items():
        overlap += min(hc, ref_g.get(g, 0))

    if hyp_total == 0 or ref_total == 0:
        return 0.0
    precision = overlap / hyp_total
    recall = overlap / ref_total
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall) * 100.0


def length_ratio_penalty(original: str, translated: str) -> float:
    """Penalize extreme compression/expansion (common MT failure signal)."""
    ow = len(_norm_tokens(original))
    tw = len(_norm_tokens(translated))
    if ow == 0 or tw == 0:
        return 0.0
    ratio = tw / ow
    if 0.55 <= ratio <= 1.85:
        return 0.0
    if ratio < 0.55:
        return min(25.0, (0.55 - ratio) * 40.0)
    return min(20.0, (ratio - 1.85) * 15.0)


def wrongful_name_substitution(original: str, translated: str) -> tuple[int, list[str]]:
    """
    Detect when source proper nouns were replaced with different ones.
    Cross-script transliteration (John → Джон) is allowed.
    """
    from engines.translation_quality import extract_proper_nouns

    src_names = extract_proper_nouns(original)
    if not src_names:
        return 0, []

    tgt_names = extract_proper_nouns(translated)
    if not tgt_names:
        return len(src_names), [f"missing:{n}" for n in src_names[:3]]

    src_lower = {n.lower() for n in src_names}
    tgt_lower = {n.lower() for n in tgt_names}

    if src_lower & tgt_lower:
        return 0, []

    # Latin source name + Cyrillic target name(s) → transliteration, not substitution
    if all(re.fullmatch(r"[A-Za-z][A-Za-z'-]*", n) for n in src_names):
        if all(_CYRILLIC_RE.search(n) for n in tgt_names):
            return 0, []

    issues: list[str] = []
    for src_n in src_names:
        if src_n.lower() in str(translated).lower():
            continue
        alien = [n for n in tgt_names if n.lower() not in src_lower]
        issues.extend(alien[:3] if alien else [f"missing:{src_n}"])

    return len(issues), issues


def missing_preserved_count(original: str, translated: str) -> int:
    from engines.translation_quality import (
        extract_abbreviations,
        extract_proper_nouns,
        extract_preserved_tokens,
        missing_preserved_tokens,
    )

    missing = missing_preserved_tokens(original, translated)
    src_names = {n.lower() for n in extract_proper_nouns(original)}
    tgt_names = extract_proper_nouns(translated)

    # Cross-script personal names: Cyrillic/Latin transliteration is OK when target has a PN.
    if src_names and tgt_names:
        missing = [m for m in missing if m.lower() not in src_names]

    # Abbreviations/brands must remain literal (NASA, Jr., etc.)
    for abbr in extract_abbreviations(original):
        if abbr.lower() not in str(translated or "").lower():
            if abbr not in missing:
                missing.append(abbr)

    return len(missing)


def _intro_name_pattern_penalty(original: str, translated: str) -> float:
    """
    Universal: penalize when a name-intro sentence collapses to a bare nominal phrase
    (e.g. 'My name is John' → 'Name of Ivan') — common low-quality MT failure mode.
    """
    src = str(original or "").strip()
    tr = str(translated or "").strip()
    if not src or not tr:
        return 0.0
    src_w = _norm_tokens(src)
    tr_w = _norm_tokens(tr)
    if len(src_w) < 3 or len(tr_w) > max(3, len(src_w) - 1):
        return 0.0
    src_has_name = bool(re.search(r"\bname\b", src, re.I)) or bool(
        re.search(r"\bmy\b.*\bname\b", src, re.I)
    )
    if not src_has_name:
        return 0.0
    tr_has_intro = bool(
        re.search(
            r"\b(i am|i'm|my name|call me|meine|je m|mi nombre|meno|men|zovut|zvy|zvyty|звati|звати|мене|меня)\b",
            tr,
            re.I,
        )
    )
    if src_has_name and not tr_has_intro and len(tr_w) <= 3:
        return 20.0
    return 0.0


def runtime_qe_penalties(
    original: str,
    translated: str,
    *,
    src_lang: str | None,
    tgt_lang: str | None,
) -> dict[str, Any]:
    """Heuristic QE penalties — no reference needed (production runtime path)."""
    missing = missing_preserved_count(original, translated)
    sub_count, sub_names = wrongful_name_substitution(original, translated)
    len_pen = length_ratio_penalty(original, translated)
    intro_pen = _intro_name_pattern_penalty(original, translated)

    total_penalty = 0.0
    total_penalty += missing * 18.0
    total_penalty += sub_count * 22.0
    total_penalty += len_pen
    total_penalty += intro_pen

    return {
        "missing_preserved_tokens": missing,
        "wrongful_substitutions": sub_count,
        "substitution_names": sub_names[:5],
        "length_ratio_penalty": round(len_pen, 2),
        "intro_pattern_penalty": round(intro_pen, 2),
        "qe_penalty": round(total_penalty, 2),
    }


def composite_benchmark_score(
    heuristic_score: float,
    reference: str | None,
    hypothesis: str,
) -> float:
    """Offline ranking: blend heuristic QE + chrF when gold reference exists."""
    if not reference or not str(reference).strip():
        return heuristic_score
    chrf = chr_f_score(reference, hypothesis)
    return round(heuristic_score * 0.45 + chrf * 0.55, 2)
