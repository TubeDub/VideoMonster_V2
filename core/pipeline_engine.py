"""Pipeline Engine — intelligent chunk conveyor (TZ #4 §1–§14).

Moves adaptive chunks between agents via per-stage buffer queues. All stages
run simultaneously: while chunk 18 is in Whisper, chunk 12 can be in Voice and
chunk 4 in Export.

The engine does NOT contain processing algorithms — it only schedules chunks,
manages buffers, preserves order, and supports pause/resume/recovery.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.chunk_manager import (
    PIPELINE_STAGES,
    ChunkManager,
    ChunkStatus,
    PipelineChunk,
)
from core.micro_validator import get_validator
from core.recovery_manager import (
    RecoveryAction,
    RecoveryManager,
    get_recovery_manager,
    recovery_enabled,
)
from engines.pipeline_orchestrator.conveyor import StageConfig, StageMetrics
from engines.pipeline_orchestrator.resource_planner import ResourcePlanner, get_planner

logger = logging.getLogger("tubedub.pipeline_engine")

_SENTINEL = object()
ChunkHandler = Callable[[PipelineChunk], PipelineChunk]


def pipeline_engine_enabled() -> bool:
    """Use adaptive chunk conveyor. Default on (TZ #4)."""
    return str(os.getenv("VM_PIPELINE_ENGINE", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


@dataclass
class PipelineEngineConfig:
    """Inputs for a conveyor run."""

    project_id: str
    source_segments: list[str] = field(default_factory=list)
    timing_map: list[dict[str, Any]] = field(default_factory=list)
    source_lang: str = "en"
    target_lang: str = "ru"
    app_dir: Any = None
    stages: tuple[str, ...] = PIPELINE_STAGES
    checkpoint_path: str = ""
    skip_stages: tuple[str, ...] = ("whisper",)  # STT usually done upstream
    chunk_size: int | None = None
    ctx: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineEngineResult:
    ok: bool
    segments: list[str] = field(default_factory=list)
    timing_map: list[dict[str, Any]] = field(default_factory=list)
    tts_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    chunks_processed: int = 0
    report: dict[str, Any] = field(default_factory=dict)


class PipelineEngine:
    """Chunk conveyor with per-stage buffers, pause/resume, and recovery."""

    def __init__(
        self,
        config: PipelineEngineConfig,
        *,
        chunk_manager: ChunkManager | None = None,
        planner: ResourcePlanner | None = None,
    ) -> None:
        self.config = config
        self.chunks = chunk_manager or ChunkManager()
        self.planner = planner or get_planner()
        self._handlers: dict[str, ChunkHandler] = {}
        self._stage_configs: list[StageConfig] = []
        self._queues: dict[str, queue.Queue] = {}
        self._metrics: dict[str, StageMetrics] = {}
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._errors: list[str] = []
        self._paused = threading.Event()
        self._paused.set()  # not paused
        self._stop = threading.Event()
        self._started_at = 0.0
        self._running = False
        self.recovery = (
            get_recovery_manager(config.project_id, app_dir=config.app_dir)
            if recovery_enabled()
            else None
        )
        self._validator = get_validator()
        self._parked_chunks: list[PipelineChunk] = []

    # ── Handler registration ─────────────────────────────────────────

    def register_handler(self, stage: str, handler: ChunkHandler) -> None:
        self._handlers[stage] = handler

    def _build_stages(self) -> list[StageConfig]:
        active = [s for s in self.config.stages if s not in self.config.skip_stages]
        stages: list[StageConfig] = []
        for name in active:
            handler = self._handlers.get(name)
            if handler is None:
                handler = self._passthrough(name)
            stages.append(StageConfig(name=name, handler=self._wrap_handler(name, handler)))
        return stages

    def _wrap_handler(self, stage: str, handler: ChunkHandler) -> Callable:
        """Wrap chunk handler with validation, recovery, and metrics (TZ #5)."""

        def _h(item: PipelineChunk) -> PipelineChunk:
            if item.is_stage_done(stage):
                return item

            item.status = ChunkStatus.RUNNING
            item.current_stage = stage
            busy_start = time.perf_counter()
            rec = self.recovery

            if rec:
                rec.track_start(item.chunk_id, stage)

            try:
                result = handler(item)
            except Exception as exc:
                busy_ms = (time.perf_counter() - busy_start) * 1000.0
                item.mark_stage(stage, ok=False, error=str(exc), busy_ms=busy_ms)
                with self._lock:
                    self._metrics[stage].errors += 1
                self._errors.append(f"{stage} chunk={item.chunk_id}: {exc}")
                if rec:
                    rec.track_end(item.chunk_id, stage)
                    return self._handle_failure(stage, item, error=str(exc))
                return item

            # Micro-validation after stage (§2, §9).
            validation = self._validator.validate_stage(stage, result)
            if not validation.ok and rec:
                action, failed_lines = rec.decide_action(
                    stage, item.chunk_id, validation=validation
                )
                if action == RecoveryAction.RETRY_LINE and failed_lines:
                    result = self._retry_lines(stage, result, failed_lines, handler)
                    validation = self._validator.validate_stage(stage, result)

            if not validation.ok and rec:
                rec.track_end(item.chunk_id, stage)
                return self._handle_failure(
                    stage, result,
                    error=";".join(i.reason for i in validation.issues[:3]),
                    validation=validation,
                )

            busy_ms = (time.perf_counter() - busy_start) * 1000.0
            result.mark_stage(stage, ok=True, busy_ms=busy_ms)
            with self._lock:
                m = self._metrics[stage]
                m.processed += 1
                m.busy_ms += busy_ms
            if busy_ms > 0:
                self.planner.record_stage_duration(
                    stage, item_count=1, duration_s=busy_ms / 1000.0
                )
            if rec:
                rec.track_end(item.chunk_id, stage)
            return result

        return _h

    def _handle_failure(
        self,
        stage: str,
        chunk: PipelineChunk,
        *,
        error: str,
        validation: Any = None,
    ) -> PipelineChunk:
        """Localise failure — retry, fallback, or park (§4–§8)."""
        rec = self.recovery
        if rec is None:
            return chunk

        action, failed_lines = rec.decide_action(
            stage, chunk.chunk_id, error=error, validation=validation
        )

        if action == RecoveryAction.RETRY_CHUNK:
            attempt = rec.record_retry(chunk.chunk_id, stage)
            wait = rec.backoff(stage, attempt)
            logger.info(
                "[RECOVERY] retry chunk=%s stage=%s attempt=%d wait=%.1fs",
                chunk.chunk_id, stage, attempt, wait,
            )
            time.sleep(wait)
            chunk.status = ChunkStatus.RETRY
            chunk.error = ""
            return chunk

        if action == RecoveryAction.FALLBACK:
            rec.register_failure(
                stage, chunk.chunk_id, error, action=RecoveryAction.FALLBACK
            )
            rec.stats.fallback_switches += 1
            chunk.payload["use_fallback"] = True
            chunk.status = ChunkStatus.RETRY
            chunk.error = ""
            return chunk

        # Park — pipeline continues with other chunks (§8).
        rec.register_failure(stage, chunk.chunk_id, error, action=RecoveryAction.PARK)
        chunk.status = ChunkStatus.SUSPENDED
        rec.parking.park(chunk, reason=error)
        self._parked_chunks.append(chunk)
        logger.warning(
            "[RECOVERY] parked chunk=%s stage=%s: %s", chunk.chunk_id, stage, error
        )
        return chunk

    def _retry_lines(
        self,
        stage: str,
        chunk: PipelineChunk,
        line_indices: list[int],
        handler: ChunkHandler,
    ) -> PipelineChunk:
        """Retry only damaged lines, not the whole chunk (§4)."""
        rec = self.recovery
        segs = list(chunk.payload.get("segments") or chunk.source_segments)
        for li in line_indices:
            if li < 0 or li >= len(segs):
                continue
            if rec:
                attempt = rec.record_retry(chunk.chunk_id, stage, line_index=li)
                wait = rec.backoff(stage, attempt)
                time.sleep(wait)
            # Re-run handler for the single line context.
            single = PipelineChunk(
                chunk_id=chunk.chunk_id,
                segment_indices=[chunk.segment_indices[li]] if li < len(chunk.segment_indices) else [li],
                source_segments=[chunk.source_segments[li]] if li < len(chunk.source_segments) else [""],
                timing_map=[chunk.timing_map[li]] if li < len(chunk.timing_map) else [],
                payload=dict(chunk.payload),
            )
            try:
                fixed = handler(single)
                new_segs = list(fixed.payload.get("segments") or fixed.source_segments)
                if new_segs:
                    segs[li] = new_segs[0]
            except Exception as exc:
                logger.warning(
                    "[RECOVERY] line retry failed chunk=%s line=%s: %s",
                    chunk.chunk_id, li, exc,
                )
        chunk.payload["segments"] = segs
        return chunk

    @staticmethod
    def _passthrough(stage: str) -> ChunkHandler:
        def _noop(chunk: PipelineChunk) -> PipelineChunk:
            return chunk
        return _noop

    # ── Run conveyor (§2–§8) ──────────────────────────────────────────

    def run(self, *, resume: bool = False) -> PipelineEngineResult:
        """Process all chunks through every active stage in parallel."""
        self._started_at = time.perf_counter()
        self._running = True
        self._errors.clear()
        self._parked_chunks.clear()

        if self.recovery:
            self.recovery.start_stall_monitor()

        # Create or restore chunks.
        if resume and self.config.checkpoint_path:
            if not self.chunks.load_checkpoint(self.config.checkpoint_path):
                resume = False
        if not resume:
            self.chunks.split_segments(
                self.config.source_segments,
                self.config.timing_map,
                project_id=self.config.project_id,
                chunk_size=self.config.chunk_size,
            )

        chunks_to_run = self.chunks.chunks_to_resume(self.config.stages)
        if not chunks_to_run:
            return self._build_result(ok=True)

        self._stage_configs = self._build_stages()
        if not self._stage_configs:
            return self._build_result(ok=False, extra_errors=["no stages configured"])

        stage_names = [s.name for s in self._stage_configs]
        for name in stage_names:
            self._queues[name] = queue.Queue(
                maxsize=self._queue_size_for(name, len(chunks_to_run))
            )
            self._metrics[name] = StageMetrics(name=name)

        # Inter-stage wiring: stage[i] output → stage[i+1] input queue.
        # First stage gets a dedicated input queue.
        input_q_name = f"_input_{stage_names[0]}"
        self._queues[input_q_name] = queue.Queue()

        # Start workers for each stage (§8 parallel).
        for i, stage in enumerate(self._stage_configs):
            in_key = input_q_name if i == 0 else stage_names[i - 1]
            out_key = stage.name
            n_workers = self._workers_for(stage.name, len(chunks_to_run))
            for wi in range(n_workers):
                t = threading.Thread(
                    target=self._stage_worker,
                    args=(stage.name, in_key, out_key, n_workers),
                    name=f"pipe-{stage.name}-{wi}-{self.config.project_id[:8]}",
                    daemon=True,
                )
                t.start()
                self._threads.append(t)

        # Seed first queue.
        for chunk in chunks_to_run:
            self._queues[input_q_name].put(chunk)
        n_first = self._workers_for(stage_names[0], len(chunks_to_run))
        for _ in range(n_first):
            self._queues[input_q_name].put(_SENTINEL)

        # Wait for all workers.
        for t in self._threads:
            t.join()

        # Collect from last stage queue.
        last_stage = stage_names[-1]
        while True:
            try:
                raw = self._queues[last_stage].get_nowait()
            except queue.Empty:
                break
            if raw is _SENTINEL:
                continue
            chunk: PipelineChunk = raw
            if chunk.error:
                chunk.status = ChunkStatus.FAILED
            else:
                chunk.status = ChunkStatus.COMPLETED
            self.chunks.update_status(chunk.chunk_id, chunk.status)

        self._running = False
        if self.recovery:
            self.recovery.stop_stall_monitor()
            # Integrity check (§12).
            integrity = self.recovery.verify_integrity(
                self.chunks.all_chunks(),
                expected_segment_count=len(self.config.source_segments),
                tts_files=list(self.config.ctx.get("tts_files") or []),
            )
            if not integrity.ok:
                for issue in integrity.issues:
                    self._errors.append(f"integrity:{issue.reason}")
            self.recovery.save_statistics()

        # AI Memory auto-learn from completed job (TZ #6 §10).
        try:
            from core.ai_memory import memory_enabled, get_memory

            if memory_enabled():
                merged_segs, _ = self.chunks.merge_results()
                mem = get_memory(
                    self.config.project_id,
                    app_dir=self.config.app_dir,
                )
                mem.learn({
                    "segments": merged_segs,
                    "source_segments": self.config.source_segments,
                    "source_lang": self.config.source_lang,
                    "target_lang": self.config.target_lang,
                    "translation_audits": self.config.ctx.get("translation_audits") or [],
                    "user_corrections": self.config.ctx.get("user_corrections") or [],
                    "voice_profiles": self.config.ctx.get("voice_profiles") or {},
                })
        except Exception as exc:
            logger.warning("[MEMORY] auto-learn failed: %s", exc)

        self._save_checkpoint_if_configured()
        return self._build_result()

    def _stage_worker(
        self,
        stage_name: str,
        in_key: str,
        out_key: str,
        n_workers: int,
    ) -> None:
        in_q = self._queues[in_key]
        out_q = self._queues[out_key]
        handler_cfg = next(s for s in self._stage_configs if s.name == stage_name)
        sentinels_seen = 0

        while not self._stop.is_set():
            # Pause support (§13).
            self._paused.wait()

            wait_start = time.perf_counter()
            try:
                raw = in_q.get(timeout=1.0)
            except queue.Empty:
                continue
            wait_ms = (time.perf_counter() - wait_start) * 1000.0

            if raw is _SENTINEL:
                sentinels_seen += 1
                out_q.put(_SENTINEL)
                if sentinels_seen >= 1:
                    break
                continue

            chunk: PipelineChunk = raw
            with self._lock:
                self._metrics[stage_name].wait_ms += wait_ms

            # Run handler (wrapped with metrics in StageConfig).
            result = handler_cfg.handler(chunk)
            out_q.put(result)

    def _workers_for(self, stage: str, chunk_count: int) -> int:
        return self.planner.plan_stage(stage, segment_count=chunk_count).workers

    def _queue_size_for(self, stage: str, chunk_count: int) -> int:
        plan = self.planner.plan_stage(stage, segment_count=chunk_count)
        return max(2, plan.queue_size)

    # ── Pause / resume (§13) ───────────────────────────────────────────

    def pause(self) -> None:
        self._paused.clear()
        self.chunks.suspend_all()
        logger.info("[PIPE] paused project=%s", self.config.project_id)

    def resume(self) -> None:
        self.chunks.resume_all()
        self._paused.set()
        logger.info("[PIPE] resumed project=%s", self.config.project_id)

    def stop(self) -> None:
        self._stop.set()
        self._paused.set()

    # ── Stall / balance hooks (§7, §9) ───────────────────────────────

    def diagnose_idle(self, stage: str) -> dict[str, Any]:
        """Check why a stage might be idle (§7)."""
        q_depth = 0
        if stage in self._queues:
            try:
                q_depth = self._queues[stage].qsize()
            except Exception:
                pass
        bottleneck = self.planner._bottleneck_stage()  # noqa: SLF001
        if bottleneck and bottleneck != stage:
            return {"cause": "upstream_bottleneck", "bottleneck": bottleneck, "queue": q_depth}
        if q_depth == 0:
            pending = [
                c.chunk_id for c in self.chunks.all_chunks()
                if c.status == ChunkStatus.WAITING and not c.is_stage_done(stage)
            ]
            if not pending:
                return {"cause": "all_chunks_past_stage", "queue": 0}
            return {"cause": "empty_queue_upstream_delay", "pending": len(pending)}
        return {"cause": "working", "queue": q_depth}

    def rebalance(self) -> None:
        """React to bottleneck by shrinking chunks (§9)."""
        bn = self.planner._bottleneck_stage()  # noqa: SLF001
        if bn:
            self.chunks.adjust_for_bottleneck(bn)

    # ── Checkpoint (§14) ───────────────────────────────────────────────

    def _save_checkpoint_if_configured(self) -> None:
        if self.config.checkpoint_path:
            try:
                self.chunks.save_checkpoint(self.config.checkpoint_path)
            except Exception as exc:
                logger.warning("[PIPE] checkpoint save failed: %s", exc)

    def _build_result(
        self,
        *,
        ok: bool | None = None,
        extra_errors: list[str] | None = None,
    ) -> PipelineEngineResult:
        segments, timing = self.chunks.merge_results()
        errors = list(self._errors) + (extra_errors or [])
        summary = self.chunks.status_summary()
        completed = summary.get("completed", 0)
        failed = summary.get("failed", 0)
        total = sum(summary.values())
        if ok is None:
            ok = failed == 0 and completed > 0
        if total > 0 and completed == 0 and failed > 0:
            ok = False
        return PipelineEngineResult(
            ok=ok,
            segments=segments,
            timing_map=timing,
            tts_files=list(self.config.ctx.get("tts_files") or []),
            errors=errors,
            chunks_processed=completed,
            report=self.report(),
        )

    def report(self) -> dict[str, Any]:
        with self._lock:
            metrics = {
                name: {
                    "processed": m.processed,
                    "errors": m.errors,
                    "utilization": m.utilization,
                    "busy_ms": round(m.busy_ms, 1),
                    "wait_ms": round(m.wait_ms, 1),
                    "queue_depth": self._queues.get(name, queue.Queue()).qsize()
                    if name in self._queues
                    else 0,
                }
                for name, m in self._metrics.items()
            }
        return {
            "project_id": self.config.project_id,
            "running": self._running,
            "paused": not self._paused.is_set(),
            "stages": [s.name for s in self._stage_configs],
            "metrics": metrics,
            "chunk_summary": self.chunks.status_summary(),
            "chunk_size": self.chunks.chunk_size,
            "planner": self.planner.to_dict(),
            "errors": list(self._errors),
        }

    def get_status(self) -> dict[str, Any]:
        status = {
            "engine": self.report(),
            "chunks": self.chunks.to_dict(),
        }
        if self.recovery:
            status["recovery"] = self.recovery.get_status()
        return status


# ── Default handlers (wrap existing engines, no algorithm changes) ─────


def build_default_handlers(ctx: dict[str, Any]) -> dict[str, ChunkHandler]:
    """Build stage handlers that delegate to existing engine modules."""

    def _cleaner(chunk: PipelineChunk) -> PipelineChunk:
        from engines.cleaner import align_segments_to_timing_map

        segs = list(chunk.payload.get("segments") or chunk.source_segments)
        tm = list(chunk.payload.get("timing_map") or chunk.timing_map)
        aligned = align_segments_to_timing_map(segs, tm)
        chunk.payload["segments"] = aligned
        chunk.payload["timing_map"] = tm
        return chunk

    def _translator(chunk: PipelineChunk) -> PipelineChunk:
        from engines.translation_pipeline import UniversalTranslationPipeline

        pipe = UniversalTranslationPipeline(quality_log=None)
        meta_out: list = []
        result = pipe.translate_segments(
            chunk.source_segments,
            chunk.timing_map,
            ctx.get("source_lang", "en"),
            ctx.get("target_lang", "ru"),
            translate_meta_out=meta_out,
        )
        chunk.payload["segments"] = list(result.segments)
        chunk.payload["timing_map"] = chunk.timing_map
        chunk.payload["translate_meta"] = meta_out
        return chunk

    def _review(chunk: PipelineChunk) -> PipelineChunk:
        # AI Review passthrough — segments already translated; review is advisory.
        return chunk

    def _timing(chunk: PipelineChunk) -> PipelineChunk:
        if ctx.get("skip_timing_adapt"):
            return chunk
        from engines.timing_aware_translation import adapt_segments_to_timing

        segs = list(chunk.payload.get("segments") or chunk.source_segments)
        adapted, _ = adapt_segments_to_timing(
            segs,
            chunk.timing_map,
            ctx.get("target_lang", "ru"),
        )
        chunk.payload["segments"] = adapted
        return chunk

    def _voice(chunk: PipelineChunk) -> PipelineChunk:
        from engines.tts import generate_audio

        segs = list(chunk.payload.get("segments") or chunk.source_segments)
        files = generate_audio(
            segs,
            voice=ctx.get("voice") or "",
            rate=ctx.get("rate") or "-5%",
            pitch=ctx.get("pitch"),
            engine=ctx.get("tts_engine") or "edge-offline",
        )
        chunk.payload["tts_files"] = files
        existing = list(ctx.get("tts_files") or [])
        ctx["tts_files"] = existing + list(files)
        return chunk

    def _mix(chunk: PipelineChunk) -> PipelineChunk:
        if ctx.get("skip_mix"):
            return chunk
        return chunk  # mix is project-level; per-chunk passthrough

    def _export(chunk: PipelineChunk) -> PipelineChunk:
        return chunk

    return {
        "cleaner": _cleaner,
        "translator": _translator,
        "review": _review,
        "timing": _timing,
        "voice": _voice,
        "mix": _mix,
        "export": _export,
    }


def run_pipeline_engine(config: PipelineEngineConfig, *, resume: bool = False) -> PipelineEngineResult:
    """Convenience entry: build engine with default handlers and run."""
    engine = PipelineEngine(config)
    for stage, handler in build_default_handlers(config.ctx).items():
        engine.register_handler(stage, handler)
    return engine.run(resume=resume)


_singleton: PipelineEngine | None = None


def get_pipeline_engine() -> PipelineEngine | None:
    return _singleton


def set_pipeline_engine(engine: PipelineEngine | None) -> None:
    global _singleton
    _singleton = engine
