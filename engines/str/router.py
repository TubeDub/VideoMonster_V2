"""Smart Translation Router — main entry point."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from engines.str.adapters import translate_via_adapter
from engines.str.compare import compare_engines
from engines.str.config import (
    DOUBTFUL_SCORE_HIGH,
    DOUBTFUL_SCORE_LOW,
    MIN_ACCEPT_QUALITY,
    MIN_QUALITY_GOOD,
    STR_VERSION,
    compare_doubtful_enabled,
    max_engine_tries,
)
from engines.str.diagnostics import engine_trend, priority_adjustment
from engines.str.knowledge_base import record_translation
from engines.str.ranking import ranked_engines_for_pair
from engines.str.types import STRTranslationResult
from engines.translation_quality_score import compute_quality_score, should_switch_route

logger = logging.getLogger("tubedub.engines.str.router")

__all__ = ["translate_with_str", "ensure_str_ready", "str_engine_rankings"]


def _norm(code: str | None) -> str:
    return (code or "en").split("-")[0].lower()


def str_engine_rankings(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
    *,
    source_text: str = "",
) -> list[dict[str, Any]]:
    """Human-readable rankings for diagnostics UI."""
    ranked = ranked_engines_for_pair(
        app_dir, src_lang, tgt_lang, source_text=source_text
    )
    out: list[dict[str, Any]] = []
    for eng, score, reason in ranked:
        trend = engine_trend(app_dir, src_lang, tgt_lang, eng.id)
        adj = priority_adjustment(app_dir, src_lang, tgt_lang, eng.id)
        out.append(
            {
                "engine": eng.id,
                "score": score + adj,
                "base_score": score,
                "trend_adjustment": adj,
                "reason": reason,
                "trend": trend,
                "offline": eng.offline,
            }
        )
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def ensure_str_ready(app_dir: Path, src_lang: str, tgt_lang: str) -> None:
    """Preload top-ranked offline engine if possible (optional prep hook)."""
    ranked = ranked_engines_for_pair(app_dir, src_lang, tgt_lang)
    for eng, _, _ in ranked:
        if not eng.offline:
            continue
        if eng.id == "marian":
            from engines.mt.stable_translate import ensure_marian_ready

            ensure_marian_ready(app_dir, src_lang, tgt_lang)
            return
        try:
            eng.translate("test", src_lang, tgt_lang)
        except Exception:
            continue
        return


def _evaluate(
    original: str,
    result: STRTranslationResult,
    *,
    src_lang: str,
    tgt_lang: str,
) -> tuple[float, dict[str, Any], bool]:
    if not result.ok:
        return 0.0, {}, True

    score, metrics = compute_quality_score(
        original,
        result.text,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
    )
    needs_fallback = score < MIN_ACCEPT_QUALITY or should_switch_route(score, metrics)
    result.quality_probability = round(score / 100.0, 3)
    mixed = float(metrics.get("mixed_language_pct", 0))
    if mixed > 8:
        result.warnings.append(f"mixed_language:{mixed}%")
    return score, metrics, needs_fallback


def _is_doubtful(score: float) -> bool:
    return DOUBTFUL_SCORE_LOW <= score < DOUBTFUL_SCORE_HIGH


def _build_meta(
    *,
    src: str,
    tgt: str,
    result: STRTranslationResult,
    quality_score: float,
    quality_details: dict[str, Any],
    engines_tried: list[str],
    retries: int,
    router_reason: str,
    str_mode: str,
    context_used: bool,
    next_context_used: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "src": src,
        "tgt": tgt,
        "engine": result.engine_id,
        "engine_version": result.engine_version,
        "route": "direct",
        "route_label": f"{src}→{tgt}",
        "direct": True,
        "pivot": None,
        "router": True,
        "str": True,
        "str_version": STR_VERSION,
        "str_mode": str_mode,
        "router_reason": router_reason,
        "context_used": context_used,
        "next_context_used": next_context_used,
        "quality_score": round(quality_score, 2),
        "quality_details": quality_details,
        "quality_probability": result.quality_probability,
        "warnings": list(result.warnings),
        "engines_tried": engines_tried,
        "mt_retries": retries,
        "elapsed_ms": round(result.elapsed_ms, 1),
        "stable_mt": False,
    }
    if extra:
        meta.update(extra)
    return meta


def translate_with_str(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path | None = None,
    context: str | None = None,
    next_context: str | None = None,
    segment_index: int = -1,
) -> tuple[str, dict[str, Any]]:
    """
    Smart Translation Router:
    1. Rank engines from Knowledge Base
    2. Try best available (prefer free offline)
    3. Auto-evaluate quality; retry next engine if poor
    4. Compare top engines when result is doubtful
    5. Record stats — no translations stored
    """
    base_dir = app_dir or Path(__file__).resolve().parent.parent.parent
    src, tgt = _norm(src_lang), _norm(tgt_lang)
    clean = " ".join(str(text or "").split()).strip()

    empty_meta: dict[str, Any] = {
        "engine": None,
        "str": True,
        "str_version": STR_VERSION,
        "router": True,
        "quality_score": 0.0,
        "engines_tried": [],
        "mt_retries": 0,
        "router_reason": "",
    }

    if not clean:
        return text, empty_meta
    if src == tgt:
        empty_meta["engine"] = "none"
        empty_meta["quality_score"] = 100.0
        return text, empty_meta

    ranked = ranked_engines_for_pair(
        base_dir, src, tgt, source_text=clean, prefer_offline=True
    )
    if not ranked:
        empty_meta["engine"] = "failed"
        empty_meta["router_reason"] = "no_engines_available"
        return text, empty_meta

    # Apply trend-based priority adjustments
    adjusted: list[tuple[Any, float, str]] = []
    for eng, score, reason in ranked:
        adj = priority_adjustment(base_dir, src, tgt, eng.id)
        adjusted.append((eng, score + adj, reason))
    adjusted.sort(key=lambda x: x[1], reverse=True)

    engines_tried: list[str] = []
    retries = 0
    best_result: STRTranslationResult | None = None
    best_score = -1.0
    best_metrics: dict[str, Any] = {}
    str_mode = "sequential"
    router_reason = adjusted[0][2] if adjusted else "default"

    max_tries = max_engine_tries()
    for eng, rank_score, reason in adjusted[:max_tries]:
        engines_tried.append(eng.id)
        result = translate_via_adapter(eng, clean, src, tgt)
        score, metrics, needs_fallback = _evaluate(clean, result, src_lang=src, tgt_lang=tgt)

        record_translation(
            base_dir,
            src_lang=src,
            tgt_lang=tgt,
            engine_id=eng.id,
            quality_score=score,
            elapsed_ms=result.elapsed_ms,
            mixed_language_pct=float(metrics.get("mixed_language_pct", 0)),
            retries=retries,
            success=result.ok and score >= MIN_ACCEPT_QUALITY,
            error=result.error,
            source_text=clean,
            quality_details=metrics,
        )

        if score > best_score and result.ok:
            best_score = score
            best_result = result
            best_metrics = metrics

        if result.ok and score >= MIN_QUALITY_GOOD and not should_switch_route(score, metrics):
            router_reason = f"str_accept top={eng.id} score={score:.1f} ({reason})"
            return result.text, _build_meta(
                src=src,
                tgt=tgt,
                result=result,
                quality_score=score,
                quality_details=metrics,
                engines_tried=engines_tried,
                retries=retries,
                router_reason=router_reason,
                str_mode="single_accept",
                context_used=bool(context and str(context).strip()),
                next_context_used=bool(next_context and str(next_context).strip()),
            )

        if not needs_fallback and result.ok:
            router_reason = f"str_ok top={eng.id} score={score:.1f}"
            return result.text, _build_meta(
                src=src,
                tgt=tgt,
                result=result,
                quality_score=score,
                quality_details=metrics,
                engines_tried=engines_tried,
                retries=retries,
                router_reason=router_reason,
                str_mode="single_ok",
                context_used=bool(context and str(context).strip()),
                next_context_used=bool(next_context and str(next_context).strip()),
            )

        retries += 1
        logger.info(
            "[STR] seg=%s %s→%s engine=%s score=%.1f — try next",
            segment_index,
            src,
            tgt,
            eng.id,
            score,
        )

    # Doubtful result — multi-engine comparison
    if (
        compare_doubtful_enabled()
        and best_result
        and _is_doubtful(best_score)
    ):
        compare_list = [eng for eng, _, _ in adjusted[:max_tries]]
        compared, cmp_meta = compare_engines(
            clean, src, tgt, compare_list, app_dir=base_dir
        )
        if compared and compared.ok:
            cmp_score = float(cmp_meta.get("best_score", 0))
            cmp_metrics = cmp_meta.get("quality_details") or {}
            if cmp_score >= best_score:
                best_result = compared
                best_score = cmp_score
                best_metrics = cmp_metrics
                str_mode = "compare"
                router_reason = f"str_compare best={compared.engine_id} score={cmp_score:.1f}"
                record_translation(
                    base_dir,
                    src_lang=src,
                    tgt_lang=tgt,
                    engine_id=compared.engine_id,
                    quality_score=cmp_score,
                    elapsed_ms=compared.elapsed_ms,
                    mixed_language_pct=float(cmp_metrics.get("mixed_language_pct", 0)),
                    retries=retries,
                    success=True,
                    source_text=clean,
                    quality_details=cmp_metrics,
                )

    if best_result and best_result.ok:
        trend = engine_trend(base_dir, src, tgt, best_result.engine_id)
        if trend.get("degrading"):
            best_result.warnings.append(f"engine_trend:{trend['direction']}")
        return best_result.text, _build_meta(
            src=src,
            tgt=tgt,
            result=best_result,
            quality_score=best_score,
            quality_details=best_metrics,
            engines_tried=engines_tried,
            retries=retries,
            router_reason=router_reason or f"str_best={best_result.engine_id}",
            str_mode=str_mode,
            context_used=bool(context and str(context).strip()),
            next_context_used=bool(next_context and str(next_context).strip()),
            extra={"segment_index": segment_index},
        )

    empty_meta["engine"] = "failed"
    empty_meta["engines_tried"] = engines_tried
    empty_meta["mt_retries"] = retries
    empty_meta["router_reason"] = "all_engines_failed"
    return text, empty_meta
