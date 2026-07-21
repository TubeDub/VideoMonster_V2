"""P212 Completeness Validator + P213 Hallucination Detector."""

from __future__ import annotations

import re

_NEG = re.compile(
    r"\b(not|never|no|n't|не|ні|ніколи|нет)\b", re.I
)


def completeness_score(source: str, translated: str, entities: list[str] | None = None) -> float:
    """Check lost entities / negations / extreme length collapse."""
    score = 1.0
    src = source or ""
    tgt = translated or ""
    if not tgt.strip() and src.strip():
        return 0.0
    # Entity loss
    for e in entities or []:
        if e and e.lower() not in tgt.lower() and e not in tgt:
            score -= 0.15
    # Negation loss
    if _NEG.search(src) and not _NEG.search(tgt):
        # Cross-lingual negation may differ — soft penalty if languages share tokens
        if any(x in tgt.lower() for x in ("not", "no", "не", "ні", "нет")):
            pass
        else:
            # Only hard-fail same-script cases
            if re.search(r"[A-Za-z]", src) and re.search(r"[A-Za-z]", tgt):
                score -= 0.25
    # Extreme shortening
    if len(src) > 40 and len(tgt) < max(8, int(len(src) * 0.25)):
        score -= 0.3
    return max(0.0, min(1.0, score))


def hallucination_score(source: str, translated: str) -> tuple[float, list[str]]:
    """
    Detect added content unlikely present in source.
    Returns (score 1=clean, warnings).
    """
    warnings: list[str] = []
    src = source or ""
    tgt = translated or ""
    # New numbers not in source
    src_nums = set(re.findall(r"\d[\d.,]*", src))
    tgt_nums = set(re.findall(r"\d[\d.,]*", tgt))
    extra_nums = tgt_nums - src_nums
    if extra_nums:
        warnings.append(f"hallucinated_numbers:{sorted(extra_nums)[:5]}")
    # Year-like additions
    src_years = set(re.findall(r"\b(19|20)\d{2}\b", src))
    tgt_years = set(re.findall(r"\b(19|20)\d{2}\b", tgt))
    extra_years = tgt_years - src_years
    if extra_years:
        warnings.append(f"hallucinated_years:{sorted(extra_years)[:5]}")
    # Large expansion without shared tokens (same language)
    src_toks = set(re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ']+", src.lower()))
    tgt_toks = set(re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ']+", tgt.lower()))
    if src_toks and tgt_toks and len(tgt) > len(src) * 1.8:
        novel = tgt_toks - src_toks
        if len(novel) > max(6, len(src_toks)):
            warnings.append("hallucinated_expansion")
    score = 1.0 - 0.35 * len(warnings)
    return max(0.0, score), warnings


def is_hallucination(source: str, translated: str) -> bool:
    score, _ = hallucination_score(source, translated)
    return score < 0.7
