"""Quality gate for Smart Segment Optimizer — rollback on failure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.smart_segment_optimizer.config import MIN_WORD_RETENTION
from engines.semantic_adaptation import validate_adaptation_quality


@dataclass
class QualityCheckResult:
    ok: bool = True
    score: float = 100.0
    issues: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "score": self.score,
            "issues": self.issues,
            "checks": self.checks,
        }


def _extract_numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", str(text or ""))


def _extract_dates(text: str) -> list[str]:
    return re.findall(
        r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{4}\b",
        str(text or ""),
    )


def validate_optimization(
    original: str,
    optimized: str,
    *,
    source_hint: str = "",
    tgt_lang: str = "ru",
    app_dir: Path | None = None,
    allowed_ms: int = 0,
    segment_ms: int = 0,
) -> QualityCheckResult:
    """Automatic quality checks per TZ; any failure => rollback."""
    result = QualityCheckResult()
    orig = str(original or "").strip()
    opt = str(optimized or "").strip()

    if opt == orig:
        result.checks["unchanged"] = True
        return result

    ok_base, note = validate_adaptation_quality(
        orig, opt, source_hint=source_hint, tgt_lang=tgt_lang
    )
    result.checks["substance"] = ok_base
    if not ok_base:
        result.issues.append(note)
        result.ok = False

    from engines.translation_quality import (
        extract_preserved_tokens,
        missing_preserved_tokens,
        name_to_tech_term_damage,
    )

    base = app_dir or Path(__file__).resolve().parent.parent.parent
    preserved = extract_preserved_tokens(orig, app_dir=base)
    missing_names = missing_preserved_tokens(source_hint or orig, opt, app_dir=base)
    result.checks["names_preserved"] = len(missing_names) == 0
    if missing_names:
        result.issues.append(f"names_missing:{','.join(missing_names[:6])}")
        result.ok = False

    tech_damage = name_to_tech_term_damage(source_hint or orig, opt)
    result.checks["no_name_to_tech"] = len(tech_damage) == 0
    if tech_damage:
        result.issues.append(f"name_to_tech:{','.join(tech_damage[:4])}")
        result.ok = False

    for tok in preserved:
        if tok.lower() not in opt.lower() and re.sub(r"\s+", "", tok.lower()) not in re.sub(
            r"\s+", "", opt.lower()
        ):
            result.checks.setdefault("entities_preserved", True)
            if tok.lower() not in (source_hint or "").lower():
                result.issues.append(f"entity_lost:{tok}")
                result.checks["entities_preserved"] = False
                result.ok = False

    orig_nums = _extract_numbers(orig)
    opt_nums = _extract_numbers(opt)
    result.checks["numbers_preserved"] = all(n in opt_nums for n in orig_nums)
    if orig_nums and not result.checks["numbers_preserved"]:
        lost = [n for n in orig_nums if n not in opt_nums]
        result.issues.append(f"numbers_lost:{','.join(lost[:6])}")
        result.ok = False

    orig_dates = _extract_dates(orig)
    opt_dates = _extract_dates(opt)
    result.checks["dates_preserved"] = all(d in opt_dates for d in orig_dates)
    if orig_dates and not result.checks["dates_preserved"]:
        result.issues.append("dates_lost")
        result.ok = False

    if opt and len(opt.split()) >= 2:
        broken = bool(re.search(r"\s{2,}", opt)) or opt.endswith(",")
        result.checks["grammar_ok"] = not broken
        if broken:
            result.issues.append("grammar_broken")
            result.ok = False

    orig_w = re.findall(r"\w+", orig.lower())
    opt_w = re.findall(r"\w+", opt.lower())
    if len(orig_w) >= 5:
        kept = len(set(orig_w) & set(opt_w))
        retention = kept / max(len(set(orig_w)), 1)
        result.checks["word_retention"] = retention >= MIN_WORD_RETENTION
        if retention < MIN_WORD_RETENTION:
            result.issues.append(f"word_retention:{retention:.2f}")
            result.ok = False

    if re.search(r"\b(він|вона|воно|he|she|it|they)\s*$", opt, re.IGNORECASE):
        if not re.search(r"[.!?…]\s*$", opt):
            result.checks["complete_sentence"] = False
            result.issues.append("incomplete_tail")
            result.ok = False
    else:
        result.checks["complete_sentence"] = True

    if allowed_ms > 0:
        from engines.semantic_adaptation import estimate_tts_duration_ms

        est = estimate_tts_duration_ms(opt, tgt_lang)
        result.checks["fits_in_segment"] = est <= allowed_ms
        if not result.checks["fits_in_segment"]:
            result.issues.append(f"still_overflow:{est}>{allowed_ms}")
            result.ok = False

    if result.ok:
        result.score = 100.0
    else:
        result.score = max(0.0, 100.0 - len(result.issues) * 15)

    return result
