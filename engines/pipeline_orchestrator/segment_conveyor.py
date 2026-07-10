"""Per-segment multi-queue conveyor — Marian → LLM → TTS (TZ §1, §4, §7).

Each segment flows independently through stage queues. While segment N+1 is in
Marian, segment N may already be in LLM or TTS.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("tubedub.pipeline_orchestrator.segment_conveyor")

_SENTINEL = object()


@dataclass
class SegmentWork:
    index: int
    source_text: str = ""
    raw_mt: str = ""
    polished: str = ""
    tts_file: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    stage_trace: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


StageFn = Callable[[SegmentWork], SegmentWork]


@dataclass
class SegmentConveyorConfig:
    marian_workers: int = 1
    llm_workers: int = 2
    tts_workers: int = 2
    marian_fn: StageFn | None = None
    llm_fn: StageFn | None = None
    tts_fn: StageFn | None = None
    on_progress: Callable[[str, int, int], None] | None = None


class SegmentConveyor:
    """Three-stage segment pipeline with non-blocking queues."""

    STAGES = ("marian", "llm", "tts")

    def __init__(self, config: SegmentConveyorConfig, *, task_id: str = "") -> None:
        self.config = config
        self.task_id = task_id
        self._queues: dict[str, queue.Queue] = {
            "marian": queue.Queue(),
            "llm": queue.Queue(),
            "tts": queue.Queue(),
            "done": queue.Queue(),
        }
        self._threads: list[threading.Thread] = []
        self._metrics: dict[str, dict[str, float]] = {
            s: {"processed": 0, "busy_ms": 0.0, "errors": 0} for s in self.STAGES
        }
        self._lock = threading.Lock()
        self._total = 0

    def run(self, items: list[SegmentWork]) -> list[SegmentWork]:
        if not items:
            return []
        self._total = len(items)
        for item in items:
            self._queues["marian"].put(item)

        workers_map = {
            "marian": max(1, self.config.marian_workers),
            "llm": max(1, self.config.llm_workers),
            "tts": max(1, self.config.tts_workers),
        }
        fn_map = {
            "marian": self.config.marian_fn,
            "llm": self.config.llm_fn,
            "tts": self.config.tts_fn,
        }

        for stage in self.STAGES:
            n = workers_map[stage]
            out_q = "llm" if stage == "marian" else ("tts" if stage == "llm" else "done")
            for wi in range(n):
                t = threading.Thread(
                    target=self._worker,
                    args=(stage, out_q, fn_map[stage]),
                    name=f"seg-{stage}-{wi}-{self.task_id[:8]}",
                    daemon=True,
                )
                t.start()
                self._threads.append(t)
            for _ in range(n):
                self._queues[stage].put(_SENTINEL)

        for t in self._threads:
            t.join()

        results: list[SegmentWork] = []
        while True:
            try:
                raw = self._queues["done"].get_nowait()
            except queue.Empty:
                break
            if raw is _SENTINEL:
                continue
            results.append(raw)
        results.sort(key=lambda x: x.index)
        return results

    def _worker(self, stage: str, out_key: str, fn: StageFn | None) -> None:
        in_q = self._queues[stage]
        out_q = self._queues[out_key]
        sentinels = 0
        while True:
            try:
                raw = in_q.get(timeout=1.0)
            except queue.Empty:
                continue
            if raw is _SENTINEL:
                sentinels += 1
                out_q.put(_SENTINEL)
                if sentinels >= 1:
                    break
                continue
            item: SegmentWork = raw
            t0 = time.perf_counter()
            try:
                if fn:
                    item = fn(item)
            except Exception as exc:
                item.error = str(exc)
                with self._lock:
                    self._metrics[stage]["errors"] += 1
                logger.warning("[SegmentConveyor] %s seg=%s: %s", stage, item.index, exc)
            busy = (time.perf_counter() - t0) * 1000.0
            item.stage_trace.append({"stage": stage, "busy_ms": round(busy, 2)})
            with self._lock:
                self._metrics[stage]["processed"] += 1
                self._metrics[stage]["busy_ms"] += busy
            if self.config.on_progress:
                try:
                    self.config.on_progress(stage, self._metrics[stage]["processed"], self._total)
                except Exception:
                    pass
            out_q.put(item)

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {"task_id": self.task_id, "metrics": dict(self._metrics), "total": self._total}
