"""Pass 4 — thin adapter over unified Language Validation service."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_GARBAGE = re.compile(r"[^\w\s\.,!?;:'\"()\-\u0400-\u04FF\u00C0-\u024F]", re.UNICODE)


@dataclass
class LanguageValidationResult:
    ok: bool
    confidence: float
    issues: list[str] = field(default_factory=list)


def validate_language(
    source: str,
    translated: str,
    *,
    source_lang: str,
    target_lang: str,
) -> LanguageValidationResult:
    """Delegate to engines.language_validation (single service)."""
    issues: list[str] = []
    src = str(source or "").strip()
    tgt = str(translated or "").strip()

    if not tgt:
        return LanguageValidationResult(ok=False, confidence=0.0, issues=["empty_translation"])

    if source_lang != target_lang and tgt == src:
        issues.append("unchanged_text")

    garbage_count = len(_GARBAGE.findall(tgt))
    if garbage_count > max(3, len(tgt) // 20):
        issues.append("garbage_characters")

    try:
        from engines.language_validation.service import (
            validate_language as unified_validate,
        )

        d = unified_validate(
            tgt,
            target_lang=target_lang,
            original=src,
            source_lang=source_lang,
            stage="translation_agent",
        )
        if not d.ok:
            issues.append(d.code or d.category or "language_issue")
            issues.extend(list(d.reasons)[:4])
        conf = float(d.target_confidence or d.confidence or 0.0)
        if issues and not d.ok:
            return LanguageValidationResult(
                ok=False,
                confidence=round(conf, 4),
                issues=issues,
            )
        if issues:
            # unchanged/garbage only — soft
            return LanguageValidationResult(
                ok=len([i for i in issues if i not in ("unchanged_text",)]) == 0
                or d.ok,
                confidence=round(max(0.0, conf - 0.1 * len(issues)), 4),
                issues=issues,
            )
        return LanguageValidationResult(ok=True, confidence=round(conf, 4), issues=[])
    except Exception:
        pass

    # Minimal fallback
    try:
        from engines.pipeline_language_gate import is_critical_language_mismatch

        bad, code = is_critical_language_mismatch(
            tgt, target_lang=target_lang, original=src
        )
        if bad and code:
            issues.append(code)
    except Exception:
        pass
    confidence = max(0.0, 1.0 - 0.25 * len(issues))
    return LanguageValidationResult(
        ok=len(issues) == 0,
        confidence=round(confidence, 4),
        issues=issues,
    )
