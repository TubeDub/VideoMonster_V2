"""P3: optional LLM polish after rule-based DSAL (never a hard dependency)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from engines.dsal.clause_coverage import compute_clause_coverage, restore_missing_clauses
from engines.dsal.core import CLAUSE_COVERAGE_MIN, analyze_duration
from engines.semantic_adaptation import estimate_tts_duration_ms

if TYPE_CHECKING:
    from engines.dsal.core import DSALResult

logger = logging.getLogger("tubedub.engines.dsal.llm_enhance")


def llm_enhance_duration(
    result: "DSALResult",
    *,
    source_hint: str = "",
    tgt_lang: str = "uk",
    slot_ms: int = 0,
) -> "DSALResult":
    """If still yellow/red and LLM is available, try expand/compress once.

    On any LLM failure → return the rule-based result unchanged (warning only).
    """
    from engines.dsal.core import DSALResult

    if slot_ms <= 0:
        return result
    band = result.analysis.band
    if band == "green":
        return result

    try:
        from engines.translation_adapt import (
            _llm_expand,
            _llm_shorten,
            llm_rephrase_available,
        )
    except Exception as exc:
        logger.debug("LLM enhance import skipped: %s", exc)
        return result

    if not llm_rephrase_available():
        return result

    current = result.text
    stages = list(result.stages)
    method = result.method

    try:
        if result.analysis.expand_required or (
            result.analysis.delta_ms > int(slot_ms * 0.10)
        ):
            cur_ms = estimate_tts_duration_ms(current, tgt_lang)
            target_ratio = min(1.8, max(1.08, slot_ms / max(cur_ms, 1)))
            candidate = _llm_expand(
                current, source_hint, target_ratio, tgt_lang=tgt_lang
            )
            stage = "llm_expand"
        elif result.analysis.compress_required or (
            result.analysis.delta_ms < -int(slot_ms * 0.10)
        ):
            cur_ms = estimate_tts_duration_ms(current, tgt_lang)
            target_ratio = max(0.55, min(0.92, slot_ms / max(cur_ms, 1)))
            candidate = _llm_shorten(
                current, source_hint, target_ratio, tgt_lang=tgt_lang
            )
            stage = "llm_compress"
        else:
            return result
    except Exception as exc:
        logger.warning("LLM DSAL enhance failed (keeping rule text): %s", exc)
        stages.append(f"llm_error:{type(exc).__name__}")
        return DSALResult(
            text=result.text,
            changed=result.changed,
            analysis=result.analysis,
            stages=stages,
            adaptation_executed=result.adaptation_executed,
            method=result.method,
            detail=result.detail + "|llm_error",
            clause_coverage=result.clause_coverage,
        )

    if not candidate:
        stages.append("llm_no_change")
        return result

    candidate = " ".join(str(candidate).split())
    if candidate == current:
        stages.append("llm_no_change")
        return result

    # Reject foreign-script corruption
    try:
        from engines.sentence_integrity import contains_foreign_script

        if contains_foreign_script(candidate, tgt_lang):
            stages.append("llm_foreign_script_rejected")
            return result
    except Exception:
        pass

    # Meaning gate
    try:
        from engines.semantic_meaning import verify_meaning_preserved

        ok, reason, _ = verify_meaning_preserved(
            source_hint, current, candidate, target_lang=tgt_lang
        )
        if not ok:
            stages.append(f"llm_meaning_rejected_{reason}")
            return result
    except Exception:
        pass

    # Re-apply critical clauses if LLM dropped them
    cov = compute_clause_coverage(source_hint, candidate)
    if cov.coverage < CLAUSE_COVERAGE_MIN or cov.missing:
        candidate, cov = restore_missing_clauses(candidate, source_hint)
        if cov.restored_phrases:
            stages.extend(f"clause_after_llm:{p}" for p in cov.restored_phrases)

    stages.append(stage)
    final = analyze_duration(slot_ms=slot_ms, text=candidate, tgt_lang=tgt_lang)
    return DSALResult(
        text=candidate,
        changed=True,
        analysis=final,
        stages=stages,
        adaptation_executed=True,
        method=f"llm_{stage}",
        detail=(
            f"band={result.analysis.band}->{final.band} "
            f"delta={result.analysis.delta_ms}->{final.delta_ms} "
            f"clause={cov.coverage}"
        ),
        clause_coverage=cov.coverage,
    )
