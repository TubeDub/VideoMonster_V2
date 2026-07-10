"""Naturalizer V2 — Quality Validator (0–100)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from engines.naturalizer_v2.bad_patterns import detect_bad_mt_patterns, has_bad_mt
from engines.naturalizer_v2.mixed_language import mixed_language_percent
from engines.semantic_translation import detect_semantic_issues
from engines.translation_quality import (
    extract_preserved_tokens,
    missing_preserved_tokens,
    is_nonsense_text,
)


@dataclass
class QualityReport:
    score: float = 100.0
    problems: list[str] = field(default_factory=list)
    mixed_language_pct: float = 0.0
    needs_retry: bool = False
    retry_reason: str = ""
    fix_count_hint: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "problems": self.problems,
            "mixed_language_pct": self.mixed_language_pct,
            "needs_retry": self.needs_retry,
            "retry_reason": self.retry_reason,
        }


def validate_naturalized_quality(
    *,
    original: str,
    raw_mt: str,
    text: str,
    src_lang: str | None,
    tgt_lang: str,
    threshold: float = 62.0,
    mixed_threshold: float = 3.0,
) -> QualityReport:
    """Score naturalness and decide if retry is needed."""
    report = QualityReport()
    tr = str(text or "").strip()
    if not tr:
        report.score = 0.0
        report.problems.append("empty")
        report.needs_retry = True
        report.retry_reason = "empty_segment"
        return report

    score = 100.0
    lang = (tgt_lang or "uk").split("-")[0].lower()

    # Mixed language
    report.mixed_language_pct = mixed_language_percent(tr, tgt_lang=lang)
    if report.mixed_language_pct > mixed_threshold:
        score -= min(40, report.mixed_language_pct * 2)
        report.problems.append(f"mixed_language:{report.mixed_language_pct}%")
        report.needs_retry = True
        report.retry_reason = "mixed_language"

    # Bad MT patterns
    bad = detect_bad_mt_patterns(tr)
    if bad:
        score -= min(35, len(bad) * 12)
        for b in bad[:5]:
            report.problems.append(f"bad_mt:{b['code']}")
        report.needs_retry = True
        if not report.retry_reason:
            report.retry_reason = "bad_mt_pattern"

    # Literal calques / semantic
    for issue in detect_semantic_issues(original, tr, source_lang=src_lang, target_lang=lang):
        score -= 10
        report.problems.append(f"semantic:{issue.get('code', 'issue')}")
        report.needs_retry = True
        if not report.retry_reason:
            report.retry_reason = "literal_calque"

    # Preserved tokens / names damaged
    missing = missing_preserved_tokens(original, tr)
    if missing:
        score -= min(25, len(missing) * 8)
        report.problems.append(f"names_damaged:{','.join(missing[:4])}")
        report.needs_retry = True
        if not report.retry_reason:
            report.retry_reason = "entity_damage"

    # Word repetition
    words = re.findall(r"\b[\w'-]+\b", tr.lower(), flags=re.UNICODE)
    for i in range(1, len(words)):
        if words[i] == words[i - 1] and len(words[i]) > 2:
            score -= 8
            report.problems.append("word_repetition")
            break

    # Nonsense / incomplete
    if is_nonsense_text(tr):
        score -= 30
        report.problems.append("nonsense")
        report.needs_retry = True
        report.retry_reason = report.retry_reason or "nonsense"

    if tr and not re.search(r"[.!?…]$", tr) and len(tr.split()) > 6:
        score -= 5
        report.problems.append("incomplete_sentence")

    # Compare to raw — if identical but bad MT detected, force retry
    if tr == raw_mt and has_bad_mt(tr):
        score -= 20
        report.problems.append("unchanged_bad_mt")
        report.needs_retry = True
        report.retry_reason = report.retry_reason or "unchanged_bad_mt"

    report.score = max(0.0, min(100.0, round(score, 1)))
    if report.score < threshold and not report.needs_retry:
        report.needs_retry = True
        report.retry_reason = report.retry_reason or "low_quality_score"

    report.fix_count_hint = len(report.problems)
    return report
