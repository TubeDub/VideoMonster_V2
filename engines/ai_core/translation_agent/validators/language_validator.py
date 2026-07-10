"""Pass 4 — detect source-language mixing and garbage output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_LATIN = re.compile(r"[A-Za-z]")
_GARBAGE = re.compile(r"[^\w\s\.,!?;:'\"()\-\u0400-\u04FF\u00C0-\u024F]", re.UNICODE)


@dataclass
class LanguageValidationResult:
    ok: bool
    confidence: float
    issues: list[str] = field(default_factory=list)


def _script_ratio(text: str, pattern: re.Pattern[str]) -> float:
    chars = [c for c in text if c.isalpha()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if pattern.match(c)) / len(chars)


def validate_language(
    source: str,
    translated: str,
    *,
    source_lang: str,
    target_lang: str,
) -> LanguageValidationResult:
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

    src_lang = (source_lang or "en").lower()[:2]
    tgt_lang = (target_lang or "ru").lower()[:2]

    cyr_tgt = _script_ratio(tgt, _CYRILLIC)
    lat_tgt = _script_ratio(tgt, _LATIN)

    if tgt_lang in ("ru", "uk") and lat_tgt > 0.55:
        issues.append("excess_latin_in_cyrillic_target")
    if tgt_lang == "en" and cyr_tgt > 0.4:
        issues.append("cyrillic_in_english_target")

    try:
        from engines.pipeline_language_gate import is_critical_language_mismatch

        bad, code = is_critical_language_mismatch(
            tgt,
            target_lang=target_lang,
            original=src,
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
