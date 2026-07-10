"""Full dub conveyor — translate chunks while TTS runs on earlier chunks (TZ §1, §7)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.pipeline_engine import PipelineEngine, PipelineEngineConfig, pipeline_engine_enabled
from core.chunk_manager import PipelineChunk
from engines.pipeline_orchestrator.translation_conveyor_runner import full_conveyor_enabled

logger = logging.getLogger("tubedub.pipeline_orchestrator.dub_conveyor")


@dataclass
class FullConveyorResult:
    ok: bool
    segments: list[str] = field(default_factory=list)
    segments_data: list[dict] = field(default_factory=list)
    tts_files: list[str] = field(default_factory=list)
    timing_map: list[dict] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    whisper_sec: float = 0.0
    marian_sec: float = 0.0
    llm_sec: float = 0.0
    tts_sec: float = 0.0


def _build_handlers(ctx: dict[str, Any]) -> dict[str, Callable]:
    """Chunk handlers: translator → voice with live progress."""
    app_dir = Path(ctx["app_dir"])
    task_id = str(ctx.get("task_id") or "")
    t0_stages: dict[str, float] = {}

    def _progress(phase: str, done: int, total: int, **extra: Any) -> None:
        try:
            from api.auto_dub_api import _update_progress_detail

            _update_progress_detail(
                task_id,
                phase=phase,
                segments_done=done,
                total_segments=total,
                conveyor_stage=phase,
                **extra,
            )
        except Exception:
            pass

    def _translator(chunk: PipelineChunk) -> PipelineChunk:
        from engines.translation_pipeline import UniversalTranslationPipeline
        from engines.cleaner import align_segments_to_timing_map

        t0 = time.perf_counter()
        _progress("translate", chunk.chunk_id, ctx.get("chunk_count", 1), live_message="Marian → LLM…")
        pipe = UniversalTranslationPipeline(app_dir=app_dir, task_id=task_id)
        meta_out: list = []

        def _cb(done: int, total: int) -> None:
            _progress("translate", done, total, translation_subphase="marian_mt")

        result = pipe.translate_segments(
            chunk.source_segments,
            chunk.timing_map,
            ctx.get("source_lang", "en"),
            ctx.get("target_lang", "ru"),
            translate_meta_out=meta_out,
            progress_cb=_cb,
        )
        segs = align_segments_to_timing_map(result.segments, chunk.timing_map)
        chunk.payload["segments"] = segs
        chunk.payload["translate_meta"] = meta_out
        chunk.payload["translation_audits"] = pipe.quality_log.records_as_dicts()
        t0_stages["translator"] = time.perf_counter() - t0
        return chunk

    def _voice(chunk: PipelineChunk) -> PipelineChunk:
        from engines.tts import generate_audio

        t0 = time.perf_counter()
        segs = list(chunk.payload.get("segments") or chunk.source_segments)
        _progress("tts", chunk.chunk_id + 1, ctx.get("chunk_count", 1), live_message="TTS…")
        files = generate_audio(
            "",
            voice=str(ctx.get("voice") or ""),
            segments=segs,
            rate=ctx.get("tts_rate"),
            pitch=ctx.get("tts_pitch"),
            engine_id=ctx.get("tts_engine") or "edge-offline",
            output_dir=Path(ctx.get("output_dir") or app_dir / "output"),
            task_id=task_id,
        )
        chunk.payload["tts_files"] = files
        chunk.payload["tts_segments"] = segs
        all_files = list(ctx.get("tts_files_acc") or [])
        all_files.extend(files)
        ctx["tts_files_acc"] = all_files
        t0_stages["voice"] = time.perf_counter() - t0
        return chunk

    def _cleaner(chunk: PipelineChunk) -> PipelineChunk:
        return chunk

    return {
        "cleaner": _cleaner,
        "translator": _translator,
        "review": lambda c: c,
        "timing": lambda c: c,
        "voice": _voice,
        "mix": lambda c: c,
        "export": lambda c: c,
    }


def run_full_dub_conveyor(
    *,
    task_id: str,
    source_segments: list[str],
    timing_map: list[dict],
    source_lang: str,
    target_lang: str,
    voice: str,
    app_dir: Path,
    output_dir: Path | None = None,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    tts_engine: str = "edge-offline",
    whisper_sec: float = 0.0,
) -> FullConveyorResult:
    """Run chunk conveyor: translate and TTS overlap per chunk."""
    if not full_conveyor_enabled() or not pipeline_engine_enabled():
        return FullConveyorResult(ok=False, errors=["conveyor_disabled"])

    if len(source_segments) < 2:
        return FullConveyorResult(ok=False, errors=["too_few_segments"])

    out_dir = output_dir or app_dir / "output"
    ctx: dict[str, Any] = {
        "task_id": task_id,
        "app_dir": app_dir,
        "output_dir": out_dir,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "voice": voice,
        "tts_rate": tts_rate,
        "tts_pitch": tts_pitch,
        "tts_engine": tts_engine,
        "tts_files_acc": [],
    }

    from engines.pipeline_orchestrator.resource_planner import get_planner

    planner = get_planner()
    chunk_plan = planner.plan_stage("translator", segment_count=len(source_segments))
    chunk_size = max(2, min(chunk_plan.batch_size * 2, 8))

    config = PipelineEngineConfig(
        project_id=task_id,
        source_segments=source_segments,
        timing_map=timing_map,
        source_lang=source_lang,
        target_lang=target_lang,
        app_dir=app_dir,
        skip_stages=("whisper",),
        stages=("cleaner", "translator", "voice"),
        chunk_size=chunk_size,
        ctx=ctx,
    )
    ctx["chunk_count"] = max(1, (len(source_segments) + chunk_size - 1) // chunk_size)

    engine = PipelineEngine(config, planner=planner)
    for stage, handler in _build_handlers(ctx).items():
        engine.register_handler(stage, handler)

    t0 = time.perf_counter()
    result = engine.run()
    elapsed = time.perf_counter() - t0

    segments = list(result.segments)
    if not segments:
        segments = source_segments

    tts_files = list(ctx.get("tts_files_acc") or [])
    segments_data: list[dict] = []
    file_idx = 0
    for i, text in enumerate(segments):
        row: dict[str, Any] = {
            "index": i,
            "text": str(text or "").strip(),
            "plain_text": str(text or "").strip(),
            "translation_text": str(text or "").strip(),
        }
        if file_idx < len(tts_files):
            row["file"] = tts_files[file_idx]
            file_idx += 1
        segments_data.append(row)

    metrics = (result.report or {}).get("metrics") or {}
    marian_sec = float(metrics.get("translator", {}).get("busy_ms", 0)) / 1000.0
    tts_sec = float(metrics.get("voice", {}).get("busy_ms", 0)) / 1000.0

    timing_payload = {
        "whisper_sec": round(whisper_sec, 1),
        "marian_sec": round(marian_sec, 1),
        "llm_sec": round(max(0.0, elapsed - marian_sec - tts_sec), 1),
        "post_sec": 0.0,
        "tts_sec": round(tts_sec, 1),
        "conveyor_elapsed_sec": round(elapsed, 1),
        "segment_count": len(source_segments),
        "chunk_size": chunk_size,
        "metrics": metrics,
    }

    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.setdefault("info", {})
                info["pipeline_conveyor_timing"] = timing_payload
                info["full_conveyor_report"] = result.report
    except Exception:
        pass

    logger.info(
        "[FullConveyor] task=%s chunks=%d segments=%d elapsed=%.1fs ok=%s",
        task_id,
        result.chunks_processed,
        len(segments),
        elapsed,
        result.ok,
    )

    return FullConveyorResult(
        ok=result.ok and bool(segments),
        segments=segments,
        segments_data=segments_data,
        tts_files=tts_files,
        timing_map=list(result.timing_map or timing_map),
        report=result.report,
        errors=list(result.errors),
        whisper_sec=whisper_sec,
        marian_sec=marian_sec,
        llm_sec=max(0.0, elapsed - marian_sec - tts_sec),
        tts_sec=tts_sec,
    )


def try_run_full_conveyor(**kwargs: Any) -> FullConveyorResult | None:
    """Safe wrapper — returns None when disabled or on failure (caller uses legacy path)."""
    if not full_conveyor_enabled():
        return None
    try:
        result = run_full_dub_conveyor(**kwargs)
        if result.ok:
            return result
        logger.warning("[FullConveyor] failed: %s", result.errors[:3])
    except Exception as exc:
        logger.warning("[FullConveyor] error: %s", exc, exc_info=True)
    return None
