"""Final LLM adaptation diagnostics report for a dub task."""

from __future__ import annotations

from typing import Any


def build_llm_adaptation_report(task_info: dict[str, Any]) -> dict[str, Any]:
    """Aggregate provider status, rewrite stats, and per-segment LLM traces."""
    segments = task_info.get("segments_data") or []
    provider_status = task_info.get("llm_provider_status") or {}
    llm_calls = task_info.get("llm_calls") or []

    requiring = 0
    rewritten = 0
    failed = 0
    iterations_total = 0
    iterations_counted = 0
    overflow_reductions: list[float] = []
    duration_improvements: list[float] = []

    per_segment: list[dict[str, Any]] = []
    for seg in segments:
        if seg.get("merged_into") is not None:
            continue
        needs = bool(seg.get("requires_llm_adaptation"))
        called = bool(seg.get("llm_called"))
        trace = seg.get("ai_adaptation_trace") or {}
        if needs:
            requiring += 1
        if called:
            rewritten += 1
        if needs and not called:
            failed += 1
        iters = int(trace.get("iterations") or seg.get("rewrite_iterations") or 0)
        if iters > 0:
            iterations_total += iters
            iterations_counted += 1
        orig = float(trace.get("original_duration_ms") or seg.get("original_duration_ms") or 0)
        new = float(trace.get("rewritten_duration_ms") or seg.get("rewritten_duration_ms") or 0)
        delta = float(trace.get("duration_delta_ms") or seg.get("duration_delta_ms") or 0)
        if orig > 0 and new > 0:
            duration_improvements.append(orig - new)
        if delta < 0:
            overflow_reductions.append(abs(delta))
        per_segment.append(
            {
                "index": seg.get("index"),
                "requires_llm_adaptation": needs,
                "llm_called": called,
                "provider": trace.get("provider") or seg.get("llm_provider") or provider_status.get("provider"),
                "model": trace.get("model") or seg.get("llm_model") or provider_status.get("model"),
                "rewrite_reason": trace.get("rewrite_reason") or seg.get("rewrite_reason"),
                "iterations": iters,
                "original_duration_ms": orig,
                "rewritten_duration_ms": new,
                "duration_delta_ms": delta,
                "compression_ratio": trace.get("compression_ratio") or seg.get("compression_ratio"),
                "provider_fatal": bool(trace.get("provider_fatal") or seg.get("provider_fatal")),
            }
        )

    success_rate = round((rewritten / requiring) * 100.0, 1) if requiring else 100.0
    return {
        "provider": provider_status.get("provider") or "",
        "model": provider_status.get("model") or "",
        "llm_available": bool(task_info.get("llm_callable")),
        "remediation": provider_status.get("remediation") or "",
        "fatal_reason": provider_status.get("fatal_reason") or "",
        "installed_models": provider_status.get("installed_models") or [],
        "segments_requiring_llm": requiring,
        "segments_rewritten": rewritten,
        "segments_failed": failed,
        "success_rate_pct": success_rate,
        "avg_iterations": round(iterations_total / iterations_counted, 2) if iterations_counted else 0.0,
        "avg_duration_improvement_ms": round(
            sum(duration_improvements) / len(duration_improvements), 1
        )
        if duration_improvements
        else 0.0,
        "avg_overflow_reduction_ms": round(
            sum(overflow_reductions) / len(overflow_reductions), 1
        )
        if overflow_reductions
        else 0.0,
        "llm_calls_total": len(llm_calls),
        "segments": per_segment,
    }
