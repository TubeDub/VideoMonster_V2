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


def finalize_living_uk_segments(
    source_segments: list[str],
    mt_lines: list[str],
    *,
    app_dir: Path | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Rule naturalizer + glossary + RU rewrite + pad strip after Marian.

    Happy Path metadata promised naturalizer; Stage 7b skipped it, so Simple
    voiced raw Marian + «саме тоді» pads (task 8fadb9dd / IMG_2790).
    """
    sources = [str(s or "") for s in (source_segments or [])]
    lines = [str(t or "") for t in (mt_lines or [])]
    n = len(sources)
    while len(lines) < n:
        lines.append("")
    lines = lines[:n]
    meta = {
        "naturalizer_executed": False,
        "naturalizer_applied": False,
        "ru_rewrites": 0,
        "pads_stripped": 0,
        "repeat_blocked_count": 0,
        "tts_bleed_blocked_count": 0,
        "gey_fixed_count": 0,
        "ru_leak_fixed_count": 0,
    }
    if not n:
        return [], meta

    polished = list(lines)
    try:
        from engines.translation_naturalizer import polish_lines

        polished = polish_lines(
            lines,
            source_segments=sources,
            tgt_lang="uk",
            src_lang="en",
            use_llm=False,
            app_dir=app_dir,
        )
        meta["naturalizer_executed"] = True
        if len(polished) != n:
            polished = (list(polished) + [""] * n)[:n]
    except Exception as exc:
        logger.warning("simple_mt naturalizer skipped: %s", exc)
        polished = list(lines)

    from engines.mt.glossary_en_uk import (
        apply_street_interview_uk,
        apply_uk_gender_lock,
        finalize_mt_text,
        restore_dropped_source_entities,
        rewrite_hey_vocative_uk,
        street_uk_honesty_gaps,
        strip_en_tokens_for_uk_tts,
    )
    from engines.text_slot_fit import prepare_uk_spoken_text
    from engines.tts_lang_lock import rewrite_russian_leak_for_uk, uk_text_has_russian_leak

    out: list[str] = []
    applied = 0
    for src, raw in zip(sources, polished):
        t = finalize_mt_text("en", "uk", raw, source=src)
        before = t
        t = prepare_uk_spoken_text(t)
        if t != before:
            meta["pads_stripped"] += 1
        t = restore_dropped_source_entities(src, t)
        t = apply_street_interview_uk(src, t)
        if t and uk_text_has_russian_leak(t):
            rewritten = rewrite_russian_leak_for_uk(t)
            rewritten = prepare_uk_spoken_text(rewritten)
            if rewritten:
                t = rewritten
                meta["ru_rewrites"] += 1
        meta["ru_leak_fixed_count"] = int(meta.get("ru_rewrites") or 0)
        if t != str(raw or "").strip():
            applied += 1
        out.append(t)

    try:
        from engines.repetition_guard import (
            dedupe_segment_texts,
            refuse_naturalized_inheritance,
            rewrite_near_repeats,
            sanitize_neighbor_bleed,
            strip_looped_clauses,
        )

        inherited: list[str] = []
        prev_nat = ""
        for i, line in enumerate(out):
            own_raw = lines[i] if i < len(lines) else ""
            fixed = refuse_naturalized_inheritance(line, prev_nat, own_raw)
            inherited.append(fixed)
            if fixed:
                prev_nat = fixed
        out = inherited
        out, _ = dedupe_segment_texts(out)
        cleaned_loops: list[str] = []
        for line in out:
            c, _ch = strip_looped_clauses(line)
            cleaned_loops.append(c)
        out = cleaned_loops
        out, blocked = rewrite_near_repeats(out, raw_mts=lines, semantics=polished)
        meta["repeat_blocked_count"] = int(blocked)
        out, bleed_n = sanitize_neighbor_bleed(
            out, raw_mts=lines, naturalized=polished, sources=sources
        )
        meta["tts_bleed_blocked_count"] = int(bleed_n)
    except Exception:
        pass

    final_out: list[str] = []
    honesty: list[str] = []
    for i, t in enumerate(out):
        src_i = sources[i] if i < len(sources) else ""
        before = t
        t = rewrite_hey_vocative_uk(t)
        t = apply_uk_gender_lock(t, gender="male")
        t = strip_en_tokens_for_uk_tts(t)
        t = apply_street_interview_uk(src_i, t)
        t = restore_dropped_source_entities(src_i, t)
        t = prepare_uk_spoken_text(t)
        if t != before and "Гей" in before:
            meta["gey_fixed_count"] += 1
        honesty.extend(street_uk_honesty_gaps(src_i, t))
        final_out.append(t)
    out = final_out

    try:
        from engines.repetition_guard import sanitize_neighbor_bleed

        out, bleed2 = sanitize_neighbor_bleed(
            out, raw_mts=lines, naturalized=polished, sources=sources
        )
        meta["tts_bleed_blocked_count"] = int(meta.get("tts_bleed_blocked_count") or 0) + int(bleed2)
    except Exception:
        pass
    # Own-source facts must survive a last bleed pass (dollar / greens / $AMOUNT).
    sealed: list[str] = []
    honesty = []
    for i, t in enumerate(out):
        src_i = sources[i] if i < len(sources) else ""
        t = apply_street_interview_uk(src_i, t)
        t = restore_dropped_source_entities(src_i, t)
        t = apply_uk_gender_lock(t, gender="male")
        t = rewrite_hey_vocative_uk(t)
        t = strip_en_tokens_for_uk_tts(t)
        t = prepare_uk_spoken_text(t)
        honesty.extend(street_uk_honesty_gaps(src_i, t))
        sealed.append(t)
    out = sealed
    if honesty:
        meta["honesty_gaps"] = sorted(set(honesty))
        meta["needs_manual_review"] = True

    meta["naturalizer_applied"] = applied > 0
    return out, meta


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
    # Stage 37: rule naturalizer runs inside run_locked_simple_mt — do not
    # clobber a True stamp from that pass.
    task_info.setdefault("naturalizer_executed", False)
    task_info.setdefault("naturalizer_applied", False)
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

    tgt0 = str(target_lang or "").split("-")[0].lower()
    if tgt0 == "uk":
        segments, live_meta = finalize_living_uk_segments(
            list(source_segments or []),
            list(segments),
            app_dir=app_dir,
        )
        if len(segments) != len(source_segments or []):
            raise RuntimeError(
                f"simple_mt living_uk parity broken: {len(segments)}!={len(source_segments or [])}"
            )
    else:
        live_meta = {}

    stats = dict(stats or {})
    stats.update(live_meta)
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
