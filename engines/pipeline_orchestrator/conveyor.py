"""Generic pipeline conveyor — stage-parallel segment processing.

Generalises the queue-based pattern from ``StreamingTextPipeline`` so any
pipeline stage can be registered without rewriting ``auto_dub_api``.

Each stage runs in its own worker thread(s), pulling ``WorkItem`` objects from
an input queue and pushing results to the next stage's queue. Stages run
*simultaneously* — segment N+1 can be in Whisper while segment N is in TTS.

This module is the structural foundation for full end-to-end conveyor mode
(Whisper → Cleaner → Translation → … → Export). Individual stage handlers are
wired by the caller; the conveyor only manages scheduling and back-pressure.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from engines.pipeline_orchestrator.resource_planner import ResourcePlanner, get_planner

logger = logging.getLogger("tubedub.pipeline_orchestrator.conveyor")

_SENTINEL = object()
StageHandler = Callable[["WorkItem"], "WorkItem"]


@dataclass
class WorkItem:
    """One segment (or batch) flowing through the conveyor."""

    segment_index: int
    payload: dict[str, Any] = field(default_factory=dict)
    queued_at: float = field(default_factory=time.perf_counter)
    stage_trace: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class StageConfig:
    name: str
    handler: StageHandler
    workers: int = 0  # 0 = use planner


@dataclass
class StageMetrics:
    name: str
    processed: int = 0
    errors: int = 0
    busy_ms: float = 0.0
    wait_ms: float = 0.0

    @property
    def utilization(self) -> float:
        total = self.busy_ms + self.wait_ms
        return round(self.busy_ms / total, 4) if total > 0 else 0.0


class PipelineConveyor:
    """Multi-stage parallel conveyor with dynamic worker counts."""

    def __init__(
        self,
        stages: list[StageConfig],
        *,
        planner: ResourcePlanner | None = None,
        task_id: str = "",
    ) -> None:
        if not stages:
            raise ValueError("PipelineConveyor requires at least one stage")
        self.stages = stages
        self.planner = planner or get_planner()
        self.task_id = task_id
        self._queues: list[queue.Queue] = [queue.Queue() for _ in range(len(stages) + 1)]
        self._threads: list[threading.Thread] = []
        self._metrics: dict[str, StageMetrics] = {
            s.name: StageMetrics(name=s.name) for s in stages
        }
        self._metrics_lock = threading.Lock()
        self._errors: list[str] = []
        self._started_at = 0.0

    def run(self, items: list[WorkItem]) -> list[WorkItem]:
        """Process all items through every stage; return final outputs in order."""
        if not items:
            return []

        self._started_at = time.perf_counter()
        segment_count = len(items)

        # Seed first queue
        for item in items:
            self._queues[0].put(item)
        workers_first = self._workers_for(self.stages[0], segment_count)
        for _ in range(workers_first):
            self._queues[0].put(_SENTINEL)

        # Start workers for each stage
        for i, stage in enumerate(self.stages):
            n_workers = self._workers_for(stage, segment_count)
            out_q = self._queues[i + 1]
            for wi in range(n_workers):
                t = threading.Thread(
                    target=self._stage_worker,
                    args=(stage, self._queues[i], out_q, n_workers),
                    name=f"conv-{stage.name}-{wi}-{self.task_id[:8]}",
                    daemon=True,
                )
                t.start()
                self._threads.append(t)

        for t in self._threads:
            t.join()

        # Collect from output queue (after last stage)
        results: list[WorkItem] = []
        out_q = self._queues[len(self.stages)]
        while True:
            try:
                item = out_q.get_nowait()
            except queue.Empty:
                break
            if item is _SENTINEL:
                continue
            results.append(item)

        results.sort(key=lambda x: x.segment_index)
        return results

    def _workers_for(self, stage: StageConfig, segment_count: int) -> int:
        if stage.workers > 0:
            return stage.workers
        return self.planner.plan_stage(stage.name, segment_count=segment_count).workers

    def _stage_worker(
        self,
        stage: StageConfig,
        in_q: queue.Queue,
        out_q: queue.Queue | None,
        n_workers: int,
    ) -> None:
        sentinels_seen = 0
        while True:
            wait_start = time.perf_counter()
            try:
                raw = in_q.get(timeout=1.0)
            except queue.Empty:
                continue
            wait_ms = (time.perf_counter() - wait_start) * 1000.0

            if raw is _SENTINEL:
                sentinels_seen += 1
                if out_q is not None:
                    out_q.put(_SENTINEL)
                if sentinels_seen >= 1:
                    break
                continue

            item: WorkItem = raw
            busy_start = time.perf_counter()
            try:
                item = stage.handler(item)
            except Exception as exc:
                item.error = str(exc)
                with self._metrics_lock:
                    self._metrics[stage.name].errors += 1
                self._errors.append(f"{stage.name} seg={item.segment_index}: {exc}")
                logger.warning(
                    "[Conveyor] %s seg=%s error: %s",
                    stage.name,
                    item.segment_index,
                    exc,
                )

            busy_ms = (time.perf_counter() - busy_start) * 1000.0
            item.stage_trace.append(
                {
                    "stage": stage.name,
                    "busy_ms": round(busy_ms, 2),
                    "wait_ms": round(wait_ms, 2),
                    "error": item.error,
                }
            )

            with self._metrics_lock:
                m = self._metrics[stage.name]
                m.processed += 1
                m.busy_ms += busy_ms
                m.wait_ms += wait_ms

            if out_q is not None:
                out_q.put(item)

        elapsed = time.perf_counter() - self._started_at
        if elapsed > 0:
            with self._metrics_lock:
                proc = self._metrics[stage.name].processed
            if proc > 0:
                self.planner.record_stage_duration(
                    stage.name, item_count=proc, duration_s=elapsed
                )

    def report(self) -> dict[str, Any]:
        with self._metrics_lock:
            metrics = {
                name: {
                    "processed": m.processed,
                    "errors": m.errors,
                    "utilization": m.utilization,
                    "busy_ms": round(m.busy_ms, 1),
                    "wait_ms": round(m.wait_ms, 1),
                }
                for name, m in self._metrics.items()
            }
        return {
            "task_id": self.task_id,
            "stages": [s.name for s in self.stages],
            "metrics": metrics,
            "errors": list(self._errors),
            "planner": self.planner.to_dict(),
        }
