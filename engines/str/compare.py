"""Multi-engine comparison when translation quality is doubtful."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from engines.mt.base import BaseMTEngine
from engines.str.adapters import translate_via_adapter
from engines.str.config import COMPARE_TOP_N
from engines.str.types import STRTranslationResult
from engines.translation_quality_score import compute_quality_score, should_switch_route

logger = logging.getLogger("tubedub.engines.str.compare")


def _score_candidate(
    original: str,
    result: STRTranslationResult,
    *,
    src_lang: str,
    tgt_lang: str,
) -> tuple[float, dict[str, Any], list[str]]:
    warnings: list[str] = []
    if not result.ok:
        return 0.0, {}, ["empty_or_error"]

    score, metrics = compute_quality_score(
        original,
        result.text,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
    )
    if should_switch_route(score, metrics):
        warnings.append("quality_switch_recommended")
    mixed = float(metrics.get("mixed_language_pct", 0))
    if mixed > 8:
        warnings.append(f"mixed_language:{mixed}%")

    result.quality_probability = score / 100.0
    result.warnings = warnings
    return score, metrics, warnings


def compare_engines(
    text: str,
    src_lang: str,
    tgt_lang: str,
    engines: list[BaseMTEngine],
    *,
    app_dir: Path,
    max_engines: int | None = None,
) -> tuple[STRTranslationResult | None, dict[str, Any]]:
    """
    Run translation on multiple engines, return best by quality score.
    """
    limit = max_engines or COMPARE_TOP_N
    candidates: list[tuple[float, STRTranslationResult, dict[str, Any]]] = []
    tried: list[str] = []

    for eng in engines[:limit]:
        tried.append(eng.id)
        result = translate_via_adapter(eng, text, src_lang, tgt_lang)
        if not result.ok:
            continue
        score, metrics, _ = _score_candidate(text, result, src_lang=src_lang, tgt_lang=tgt_lang)
        candidates.append((score, result, metrics))
        logger.debug("[STR/compare] %s score=%.1f", eng.id, score)

    if not candidates:
        return None, {"engines_compared": tried, "best_score": 0.0}

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_result, best_metrics = candidates[0]
    return best_result, {
        "engines_compared": tried,
        "best_score": round(best_score, 2),
        "best_engine": best_result.engine_id,
        "quality_details": best_metrics,
        "compare_count": len(candidates),
    }
