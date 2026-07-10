"""Translation conveyor — Marian → LLM queues with batching and sliding context.

Enabled via ``VM_PIPELINE_CONVEYOR=1``. Uses :class:`PipelineConveyor` so Marian
and LLM stages overlap: segment N+1 can be in Marian while segment N is in LLM.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from engines.pipeline_orchestrator.conveyor import (
    PipelineConveyor,
    StageConfig,
    WorkItem,
)
from engines.pipeline_orchestrator.marian_result_cache import get_cached, put_cached
from engines.pipeline_orchestrator.resource_planner import (
    STAGE_AI_ADAPTATION,
    STAGE_TRANSLATION,
    get_planner,
)
from engines.pipeline_orchestrator.sliding_context import build_sliding_context
from engines.pipeline_orchestrator.stage_retry import run_with_retry
from engines.pipeline_orchestrator.translation_batch import (
    TranslationBatch,
    build_translation_batches,
    split_batch_translation,
)

logger = logging.getLogger("tubedub.pipeline_orchestrator.translation_conveyor")

STAGE_MARIAN = "marian"
STAGE_LLM = "llm_naturalize"


def conveyor_enabled() -> bool:
    val = os.getenv("VM_PIPELINE_CONVEYOR", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def full_conveyor_enabled() -> bool:
    """End-to-end translate→TTS chunk overlap (Whisper must be done upstream)."""
    if not conveyor_enabled():
        return False
    val = os.getenv("VM_FULL_PIPELINE_CONVEYOR", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def marian_worker_count() -> int:
    """TZ §5: CPU cores - 1 for Marian workers."""
    _apply_cpu_thread_tuning()
    try:
        import os as _os

        cores = _os.cpu_count() or 2
        return max(1, int(os.getenv("VM_MARIAN_WORKERS", str(max(1, cores - 1)))))
    except Exception:
        return 2


def _apply_cpu_thread_tuning() -> None:
    """TZ §10: use available CPU threads for local Marian (PyTorch)."""
    if os.getenv("VM_TORCH_THREADS", "").strip().isdigit():
        n = int(os.getenv("VM_TORCH_THREADS", "0"))
    else:
        n = max(1, (os.cpu_count() or 2) - 1)
    try:
        import torch

        torch.set_num_threads(max(1, n))
        if hasattr(torch, "set_num_interop_threads"):
            torch.set_num_interop_threads(max(1, min(4, n // 2 or 1)))
    except Exception:
        pass


def llm_worker_count() -> int:
    """TZ §5: configurable LLM parallelism."""
    try:
        return max(1, int(os.getenv("VM_LLM_WORKERS", "2")))
    except Exception:
        return 2


@dataclass
class MarianConveyorResult:
    group_results: list[tuple[list[int], str, dict[str, Any], float]]
    translation_sec: float
    cache_hits: int = 0
    conveyor_report: dict[str, Any] = field(default_factory=dict)


@dataclass
class LlmConveyorResult:
    polished: list[str]
    llm_ms: list[float]
    naturalizer_meta: list[dict[str, Any]]
    naturalizer_reasons: list[list[str]]
    llm_sec: float
    conveyor_report: dict[str, Any] = field(default_factory=dict)


def run_marian_conveyor(
    groups: list[list[int]],
    mt_segments: Sequence[str],
    source_segments: Sequence[str],
    *,
    src_lang: str,
    tgt_lang: str,
    app_dir: Path,
    task_id: str = "",
    stable: bool = False,
    progress_cb: Callable[[int, int], None] | None = None,
) -> MarianConveyorResult:
    """Translate merge-groups through Marian queue with batching + cache."""
    from engines.translation import translate_text_traced

    t0 = time.perf_counter()
    cache_hits = 0
    done = {"n": 0}
    lock = __import__("threading").Lock()

    def _translate_one(group: list[int], prev_source: str) -> tuple[list[int], str, dict[str, Any], float]:
        nonlocal cache_hits
        gt0 = time.perf_counter()
        phrase = " ".join(
            str(mt_segments[i] or "").strip()
            for i in group
            if str(mt_segments[i] or "").strip()
        ).strip()
        if not phrase:
            return group, "", {}, 0.0

        cached = get_cached(
            phrase, src_lang, tgt_lang, app_dir=app_dir, context=prev_source or ""
        )
        if cached is not None:
            with lock:
                cache_hits += 1
            meta = {"engine": "marian", "route": "cache", "cached": True}
            return group, cached, meta, (time.perf_counter() - gt0) * 1000.0

        next_source = ""
        if group:
            last = group[-1]
            if last + 1 < len(mt_segments):
                next_source = str(mt_segments[last + 1] or "").strip()

        def _do_translate() -> tuple[str, dict[str, Any]]:
            return translate_text_traced(
                phrase,
                src_lang,
                tgt_lang,
                context=prev_source or None,
                next_context=next_source or None,
                app_dir=app_dir,
                segment_index=group[0] if group else -1,
                source_original=" ".join(
                    str(source_segments[i] or "").strip() for i in group
                ).strip()
                or None,
            )

        retry = run_with_retry(
            _do_translate,
            max_attempts=3,
            stage="marian",
        )
        if not retry.ok:
            return group, "", {"engine": "error", "error": retry.error}, 0.0

        tr_phrase, meta = retry.value
        tr_phrase = str(tr_phrase or "").strip()
        if tr_phrase:
            put_cached(
                phrase,
                src_lang,
                tgt_lang,
                tr_phrase,
                app_dir=app_dir,
                context=prev_source or "",
            )
        return group, tr_phrase, dict(meta or {}), (time.perf_counter() - gt0) * 1000.0

    def _marian_handler(item: WorkItem) -> WorkItem:
        group = list(item.payload.get("group") or [])
        prev = str(item.payload.get("prev_source") or "")
        g, text, meta, ms = _translate_one(group, prev)
        item.payload["group"] = g
        item.payload["translated"] = text
        item.payload["meta"] = meta
        item.payload["elapsed_ms"] = ms
        with lock:
            done["n"] += 1
            if progress_cb:
                try:
                    progress_cb(done["n"], len(groups))
                except Exception:
                    pass
        return item

    items = [
        WorkItem(
            segment_index=i,
            payload={"group": g, "prev_source": ""},
        )
        for i, g in enumerate(groups)
    ]

    # Seed sequential context per group order for Marian.
    prev_ctx = ""
    for item in items:
        item.payload["prev_source"] = prev_ctx
        g = item.payload["group"]
        prev_ctx = " ".join(
            str(mt_segments[j] or "").strip() for j in g if str(mt_segments[j] or "").strip()
        ).strip() or prev_ctx

    workers = 1 if stable else marian_worker_count()
    conveyor = PipelineConveyor(
        [StageConfig(name=STAGE_MARIAN, handler=_marian_handler, workers=workers)],
        task_id=task_id,
        planner=get_planner(),
    )
    out_items = conveyor.run(items)
    group_results: list[tuple[list[int], str, dict[str, Any], float]] = []
    for item in out_items:
        p = item.payload
        group_results.append(
            (
                list(p.get("group") or []),
                str(p.get("translated") or ""),
                dict(p.get("meta") or {}),
                float(p.get("elapsed_ms") or 0.0),
            )
        )

    return MarianConveyorResult(
        group_results=group_results,
        translation_sec=time.perf_counter() - t0,
        cache_hits=cache_hits,
        conveyor_report=conveyor.report(),
    )


def run_llm_conveyor(
    raw_lines: Sequence[str],
    source_segments: Sequence[str],
    *,
    tgt_lang: str,
    src_lang: str,
    app_dir: Path,
    task_id: str = "",
    use_llm: bool = False,
    entity_maps: list[dict[str, str]] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> LlmConveyorResult:
    """Parallel LLM naturalization with sliding context (prev/current/next)."""
    from engines.translation_naturalizer import polish_segment_detailed

    n = len(raw_lines)
    polished: list[str] = [""] * n
    llm_ms: list[float] = [0.0] * n
    meta_out: list[dict[str, Any]] = [{} for _ in range(n)]
    reasons_out: list[list[str]] = [[] for _ in range(n)]
    t0 = time.perf_counter()
    done = {"n": 0}
    lock = __import__("threading").Lock()

    def _polish_index(i: int) -> tuple[int, str, float, dict[str, Any], list[str]]:
        raw_mt = str(raw_lines[i] or "")
        original = str(source_segments[i] if i < len(source_segments) else "")
        entity_map = entity_maps[i] if entity_maps and i < len(entity_maps) else None
        ctx = build_sliding_context(i, source_segments, window=1)
        prev = ctx.previous or ctx.window_before or None
        t_llm = time.perf_counter()

        def _do() -> Any:
            return polish_segment_detailed(
                raw_mt,
                original=original,
                tgt_lang=tgt_lang,
                src_lang=src_lang,
                prev_context=prev,
                app_dir=app_dir,
                use_llm=use_llm,
                entity_token_map=entity_map,
            )

        retry = run_with_retry(_do, max_attempts=2, stage="llm_naturalize")
        if not retry.ok:
            return i, raw_mt, 0.0, {"error": retry.error}, ["retry_failed"]
        result = retry.value
        ms = (time.perf_counter() - t_llm) * 1000.0 if use_llm else 0.0
        return (
            i,
            result.text,
            ms,
            {
                "mixed_language_pct": result.mixed_language_pct,
                "retry_reason": result.retry_reason,
                "problems": list(result.problems),
                "fix_count": result.fix_count,
                "quality_score": result.quality_score,
                "restored_entities": list(result.restored_entities),
                "warnings": list(result.warnings),
                "retried": result.retried,
                "sliding_context": ctx.as_prompt_block(),
            },
            list(result.reasons),
        )

    workers = llm_worker_count()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_polish_index, i): i for i in range(n)}
        for fut in as_completed(futures):
            i, text, ms, meta, reasons = fut.result()
            polished[i] = text
            llm_ms[i] = ms
            meta_out[i] = meta
            reasons_out[i] = reasons
            with lock:
                done["n"] += 1
                if progress_cb:
                    try:
                        progress_cb(done["n"], n)
                    except Exception:
                        pass

    from engines.translation_naturalizer import dedupe_consecutive_similar

    polished = dedupe_consecutive_similar(polished)
    return LlmConveyorResult(
        polished=polished,
        llm_ms=llm_ms,
        naturalizer_meta=meta_out,
        naturalizer_reasons=reasons_out,
        llm_sec=time.perf_counter() - t0,
        conveyor_report={"workers": workers, "segments": n},
    )


def run_batched_marian_groups(
    groups: list[list[int]],
    mt_segments: Sequence[str],
    *,
    src_lang: str,
    tgt_lang: str,
    app_dir: Path,
    timing_map: Sequence[Any] | None = None,
) -> dict[int, str]:
    """Optional: merge groups into token batches, translate once, split back."""
    from engines.translation import translate_text_traced

    flat_indices: list[int] = []
    flat_texts: list[str] = []
    for g in groups:
        for idx in g:
            flat_indices.append(idx)
            flat_texts.append(str(mt_segments[idx] or "").strip())

    batches = build_translation_batches(flat_texts, timing_map)
    out: dict[int, str] = {}
    idx_map = {j: flat_indices[j] for j in range(len(flat_indices))}

    for batch in batches:
        real_indices = [idx_map[j] for j in batch.segment_indices]
        phrase = batch.combined_source
        if not phrase:
            continue
        cached = get_cached(phrase, src_lang, tgt_lang, app_dir=app_dir)
        if cached is not None:
            split = split_batch_translation(batch, cached)
        else:
            tr, _meta = translate_text_traced(
                phrase, src_lang, tgt_lang, app_dir=app_dir
            )
            put_cached(phrase, src_lang, tgt_lang, tr, app_dir=app_dir)
            split = split_batch_translation(batch, tr)
        for local_j, seg_idx in enumerate(batch.segment_indices):
            global_idx = idx_map.get(seg_idx, seg_idx)
            out[global_idx] = split.get(seg_idx, split.get(local_j, ""))
    return out


def run_marian_llm_pipeline_conveyor(
    groups: list[list[int]],
    mt_segments: Sequence[str],
    source_segments: Sequence[str],
    raw_by_index: list[str],
    *,
    src_lang: str,
    tgt_lang: str,
    app_dir: Path,
    task_id: str = "",
    stable: bool = False,
    use_llm: bool = False,
    entity_maps: list[dict[str, str]] | None = None,
    marian_progress: Callable[[int, int], None] | None = None,
    llm_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[tuple[list[int], str, dict[str, Any], float]], LlmConveyorResult]:
    """Chain Marian → LLM in one multi-stage conveyor (TZ §7 overlap)."""
    marian = run_marian_conveyor(
        groups,
        mt_segments,
        source_segments,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        app_dir=app_dir,
        task_id=task_id,
        stable=stable,
        progress_cb=marian_progress,
    )
    for group, tr_phrase, _meta, _ms in marian.group_results:
        if not tr_phrase:
            continue
        if len(group) == 1:
            raw_by_index[group[0]] = tr_phrase
        else:
            from engines.cleaner import split_by_timing_map

            parts = split_by_timing_map(tr_phrase, [])
            for j, idx in enumerate(group):
                if j < len(parts):
                    raw_by_index[idx] = parts[j]

    llm = run_llm_conveyor(
        raw_by_index,
        source_segments,
        tgt_lang=tgt_lang,
        src_lang=src_lang,
        app_dir=app_dir,
        task_id=task_id,
        use_llm=use_llm,
        entity_maps=entity_maps,
        progress_cb=llm_progress,
    )
    return marian.group_results, llm
