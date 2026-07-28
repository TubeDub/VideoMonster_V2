# -*- coding: utf-8 -*-
"""Locked Simple MT path — Marian batch + cache only (no Qwen / AI-Core agent).

Stage 7b: UI and `_run_pipeline` must never enter translation_agent / LLM
adaptation when Simple / Happy Path is on.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.simple_mt_path")


def use_locked_simple_mt(task_info: dict[str, Any] | None = None) -> bool:
    """True → only translate_segments_batch; never agent / Qwen / streaming text."""
    info = dict(task_info or {})
    if info.get("simple_pipeline") or info.get("happy_path"):
        return True
    try:
        from engines.happy_path import is_simple_mode, skip_advanced_text_shorteners
        from engines.simple_dub_pipeline import is_simple_pipeline

        if is_simple_pipeline(info) or is_simple_mode(info):
            return True
        if skip_advanced_text_shorteners(info):
            return True
    except Exception:
        pass
    mode = str(info.get("user_mode") or info.get("vm_user_mode") or "").strip().lower()
    return mode in ("basic", "simple", "")


def resolve_translate_method(stats: dict[str, Any] | None) -> str:
    st = dict(stats or {})
    engine = str(st.get("mt_engine") or "").lower()
    hits = int(st.get("mt_cache_hits") or 0)
    misses = int(st.get("mt_cache_misses") or 0)
    if engine in ("job_cache", "cache") or (hits > 0 and misses == 0 and int(st.get("mt_calls") or 0) == 0):
        return "mt_cache"
    if "marian" in engine:
        return "marian_batch"
    return str(st.get("mt_path") or "marian_batch")


def build_simple_mt_ui_timing(
    *,
    subphase: str,
    wall_sec: float = 0.0,
    segments_done: int = 0,
    segments_total: int = 0,
    cache_mode: bool = False,
) -> dict[str, Any]:
    """Honest Simple translation UI: Marian/cache only; Qwen hidden/skipped."""
    label = "Кэш перевода" if cache_mode else "Marian MT"
    marian_status = "done" if subphase == "done" else "active"
    if subphase == "pending":
        marian_status = "pending"
    return {
        "marian_sec": round(float(wall_sec or 0), 2),
        "llm_adaptation_sec": 0.0,
        "validation_sec": 0.0,
        "post_processing_sec": 0.0,
        "translation_total_sec": round(float(wall_sec or 0), 2),
        "segment_count": int(segments_total or 0),
        "marian_segments_done": int(segments_done or 0),
        "llm_segments_done": 0,
        "current_subphase": "done" if subphase == "done" else "marian_mt",
        "ui_buckets": {
            "marian_mt": round(float(wall_sec or 0), 2),
            "llm_adaptation": 0.0,
            "post_processing": 0.0,
        },
        "phase_status": {
            "marian_mt": marian_status,
            "llm_adaptation": "skipped",
            "post_processing": "skipped",
        },
        "hidden_buckets": ["llm_adaptation", "post_processing"],
        "segment_stats": {
            "marian_mt": {
                "segments": int(segments_done or 0),
                "sec": round(float(wall_sec or 0), 1),
                "avg_sec_per_segment": round(
                    float(wall_sec or 0) / max(int(segments_done or segments_total or 1), 1),
                    3,
                ),
                "status": marian_status,
            },
            "llm_adaptation": {
                "segments": 0,
                "sec": 0.0,
                "avg_sec_per_segment": 0.0,
                "status": "skipped",
            },
            "post_processing": {
                "segments": 0,
                "sec": 0.0,
                "avg_sec_per_segment": 0.0,
                "status": "skipped",
            },
        },
        "ui_labels": {
            "marian_mt": label,
            "llm_adaptation": "Qwen / LLM Adaptation",
            "post_processing": "Post-processing",
        },
        "llm_adaptation_used": False,
        "simple_mt_locked": True,
    }


def stamp_simple_mt_lock(task_info: dict[str, Any]) -> dict[str, Any]:
    """Force flags so no later stage re-enters agent/Qwen for Simple."""
    task_info["simple_mt_locked"] = True
    task_info["translation_agent_path"] = False
    task_info["llm_adaptation_used"] = False
    task_info["tps_skip_orchestrator"] = True
    task_info["naturalizer_executed"] = False
    task_info["naturalizer_applied"] = False
    return task_info


def run_locked_simple_mt(
    source_segments: list[str],
    source_lang: str,
    target_lang: str,
    *,
    app_dir: Path,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """cache → Marian batch → stats. Never calls Qwen / translation agent."""
    from engines.mt_batch import translate_segments_batch
    from engines.mt_cache import default_cache_dir

    t0 = time.perf_counter()
    n = len(source_segments or [])

    def _prog(done: int, total: int) -> None:
        if on_progress is None:
            return
        wall = time.perf_counter() - t0
        # Heuristic: early progress with zero calls → likely serving cache.
        ui = build_simple_mt_ui_timing(
            subphase="marian_mt",
            wall_sec=wall,
            segments_done=done,
            segments_total=total,
            cache_mode=False,
        )
        try:
            on_progress(done, total, ui)
        except Exception:
            pass

    segments, stats = translate_segments_batch(
        list(source_segments or []),
        source_lang,
        target_lang,
        batch_size=10,
        cache_dir=default_cache_dir(),
        app_dir=app_dir,
        prefer_marian=True,
        on_progress=_prog if on_progress else None,
    )
    if len(segments) != len(source_segments or []):
        raise RuntimeError(
            f"simple_mt parity broken: {len(segments)}!={len(source_segments or [])}"
        )

    stats = dict(stats or {})
    stats["mt_wall_sec"] = round(
        float(stats.get("mt_wall_sec") or (time.perf_counter() - t0)), 3
    )
    method = resolve_translate_method(stats)
    stats["translate_method"] = method
    stats["mt_path"] = method
    stats["translation_agent_path"] = False
    stats["llm_adaptation_used"] = False
    stats["simple_mt_locked"] = True

    cache_mode = method == "mt_cache"
    stats["translation_timing"] = build_simple_mt_ui_timing(
        subphase="done",
        wall_sec=float(stats.get("mt_wall_sec") or 0),
        segments_done=n,
        segments_total=n,
        cache_mode=cache_mode,
    )
    logger.info(
        "simple_mt_locked: method=%s wall=%.2fs engine=%s hits=%s misses=%s calls=%s",
        method,
        float(stats.get("mt_wall_sec") or 0),
        stats.get("mt_engine"),
        stats.get("mt_cache_hits"),
        stats.get("mt_cache_misses"),
        stats.get("mt_calls"),
    )
    return segments, stats
