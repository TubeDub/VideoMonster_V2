"""LLM Orchestrator — dispatch segments to models without idle time.

The orchestrator:
* Routes each task to the best model for its difficulty (router).
* Keeps models busy — idle models immediately receive the next queued task.
* Uses backup models only on timeout / low-confidence (never every segment).
* Respects global circuit breakers — does not bypass quality gates.
* Never simplifies or skips adaptation for speed.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from engines.llm_orchestrator.model_pool import LLMModelInfo, LLMModelPool, get_model_pool
from engines.llm_orchestrator.router import (
    SegmentDifficulty,
    assess_segment_difficulty,
    backup_model,
    route_segment,
)
from engines.pipeline_orchestrator.resource_planner import get_planner

logger = logging.getLogger("tubedub.llm_orchestrator")

_LLM_CALL = Callable[..., tuple[str | None, Exception | None, dict[str, Any]]]


def _orchestrator_enabled() -> bool:
    raw = str(os.getenv("VM_LLM_ORCHESTRATOR", "1")).strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class LLMTask:
    """One adaptation / rewrite request for a single segment."""

    segment_index: int
    prompt: str
    system: str = ""
    source_text: str = ""
    translated_text: str = ""
    target_lang: str = ""
    context_before: str = ""
    context_after: str = ""
    max_tokens: int = 512
    temperature: float = 0.2
    stage: str = "ai_adaptation"
    task_id: str = ""
    allow_backup: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMTaskResult:
    ok: bool
    text: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    segment_index: int = -1
    used_backup: bool = False
    skip_reason: str = ""
    error: str = ""
    difficulty: SegmentDifficulty | None = None


class LLMOrchestrator:
    """Multi-model dispatcher with quality-first routing."""

    def __init__(
        self,
        pool: LLMModelPool | None = None,
        *,
        llm_call: _LLM_CALL | None = None,
    ) -> None:
        self._pool = pool or get_model_pool()
        self._llm_call = llm_call or self._default_llm_call
        self._task_queue: queue.Queue[LLMTask | object] = queue.Queue()
        self._results: dict[int, LLMTaskResult] = {}
        self._results_lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._stop = threading.Event()
        self._started = False
        self._stats = {
            "dispatched": 0,
            "completed": 0,
            "backup_used": 0,
            "circuit_skipped": 0,
            "errors": 0,
        }
        self._stats_lock = threading.Lock()

    @staticmethod
    def _default_llm_call(
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
        system: str | None = None,
        model: str | None = None,
        segment: int | None = None,
        timeout: float | None = None,
    ) -> tuple[str | None, Exception | None, dict[str, Any]]:
        from engines.translation_adapt import _llm_chat_once

        return _llm_chat_once(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            model=model,
            segment=segment,
        )

    def _worker_count(self) -> int:
        planner = get_planner()
        plan = planner.plan_stage("ai_adaptation")
        return max(1, plan.workers)

    def start(self) -> None:
        if self._started:
            return
        self._stop.clear()
        n = self._worker_count()
        for i in range(n):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"llm-orch-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        self._started = True
        logger.info("[LLM Orchestrator] started %d workers", n)

    def stop(self, *, timeout: float = 30.0) -> None:
        self._stop.set()
        for _ in self._workers:
            self._task_queue.put(None)
        for t in self._workers:
            t.join(timeout=timeout)
        self._workers.clear()
        self._started = False

    def submit(self, task: LLMTask) -> None:
        if not self._started:
            self.start()
        self._task_queue.put(task)
        with self._stats_lock:
            self._stats["dispatched"] += 1

    def submit_batch(self, tasks: list[LLMTask]) -> None:
        for t in tasks:
            self.submit(t)

    def run_sync(self, task: LLMTask) -> LLMTaskResult:
        """Execute one task inline (no queue) — for integration with existing code."""
        return self._execute_task(task)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._task_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            assert isinstance(item, LLMTask)
            result = self._execute_task(item)
            with self._results_lock:
                self._results[item.segment_index] = result
            with self._stats_lock:
                self._stats["completed"] += 1

    def _execute_task(self, task: LLMTask) -> LLMTaskResult:
        if not _orchestrator_enabled():
            return self._fallback_single_model(task)

        # Circuit breaker — quality gate: skip LLM but report reason (caller keeps text)
        try:
            from engines.translation_adapt import circuit_open
            from engines.ai_core.llm_gateway import can_call_llm

            if circuit_open():
                with self._stats_lock:
                    self._stats["circuit_skipped"] += 1
                return LLMTaskResult(
                    ok=False,
                    segment_index=task.segment_index,
                    skip_reason="llm_circuit_open",
                )
            allowed, reason = can_call_llm(task.task_id, task.segment_index)
            if not allowed:
                with self._stats_lock:
                    self._stats["circuit_skipped"] += 1
                return LLMTaskResult(
                    ok=False,
                    segment_index=task.segment_index,
                    skip_reason=reason or "llm_gated",
                )
        except Exception:
            pass

        self._pool.discover()
        difficulty = assess_segment_difficulty(
            task.source_text,
            task.translated_text,
            target_lang=task.target_lang,
            context_before=task.context_before,
            context_after=task.context_after,
        )

        primary = route_segment(
            self._pool,
            difficulty,
            allow_light=self._allow_light_models(),
            require_adequate=True,
        )
        if not primary:
            return self._fallback_single_model(task, difficulty=difficulty)

        result = self._call_model(task, primary, difficulty)
        if result.ok:
            return result

        # Backup path — only on failure, never preemptively
        if task.allow_backup and result.error:
            backup = backup_model(self._pool, primary)
            if backup:
                logger.info(
                    "[LLM Orchestrator] seg=%s backup %s -> %s (%s)",
                    task.segment_index,
                    primary.name,
                    backup.name,
                    result.error,
                )
                backup_result = self._call_model(task, backup, difficulty, used_backup=True)
                if backup_result.ok:
                    with self._stats_lock:
                        self._stats["backup_used"] += 1
                    return backup_result

        with self._stats_lock:
            self._stats["errors"] += 1
        return result

    def _allow_light_models(self) -> bool:
        raw = str(os.getenv("VM_LLM_ORCH_ALLOW_LIGHT", "0")).strip().lower()
        return raw in ("1", "true", "yes")

    def _call_model(
        self,
        task: LLMTask,
        model: LLMModelInfo,
        difficulty: SegmentDifficulty,
        *,
        used_backup: bool = False,
    ) -> LLMTaskResult:
        self._pool.acquire(model.name)

        planner = get_planner()
        timeout_scale = planner.plan_stage("ai_adaptation").timeout_scale
        base_timeout = 45.0
        try:
            from engines.translation_adapt import _llm_call_timeout

            base_timeout = float(_llm_call_timeout())
        except Exception:
            pass
        timeout = base_timeout * timeout_scale

        t0 = time.monotonic()
        try:
            from engines.translation_adapt import set_llm_context

            set_llm_context(segment=task.segment_index, stage=task.stage)
        except Exception:
            pass

        text, exc, meta = self._llm_call(
            task.prompt,
            max_tokens=task.max_tokens,
            temperature=task.temperature,
            system=task.system or None,
            model=model.name,
            segment=task.segment_index,
            timeout=timeout,
        )
        latency_ms = (time.monotonic() - t0) * 1000.0
        ok = bool(text) and exc is None
        self._pool.get(model.name)  # ensure registered
        m = self._pool.get(model.name)
        if m:
            m.record_call(latency_ms=latency_ms, ok=ok)
        self._pool.release(model.name)

        planner.record_stage_duration(
            "ai_adaptation", item_count=1, duration_s=latency_ms / 1000.0
        )

        if ok:
            return LLMTaskResult(
                ok=True,
                text=str(text),
                model=model.name,
                provider=str(meta.get("provider") or model.provider),
                latency_ms=latency_ms,
                segment_index=task.segment_index,
                used_backup=used_backup,
                difficulty=difficulty,
            )
        return LLMTaskResult(
            ok=False,
            segment_index=task.segment_index,
            model=model.name,
            latency_ms=latency_ms,
            used_backup=used_backup,
            error=str(exc or "empty_response"),
            difficulty=difficulty,
        )

    def _fallback_single_model(
        self,
        task: LLMTask,
        *,
        difficulty: SegmentDifficulty | None = None,
    ) -> LLMTaskResult:
        """Preserve legacy single-model path when orchestrator disabled."""
        t0 = time.monotonic()
        text, exc, meta = self._llm_call(
            task.prompt,
            max_tokens=task.max_tokens,
            temperature=task.temperature,
            system=task.system or None,
            segment=task.segment_index,
        )
        latency_ms = (time.monotonic() - t0) * 1000.0
        ok = bool(text) and exc is None
        return LLMTaskResult(
            ok=ok,
            text=str(text or ""),
            model=str(meta.get("model") or ""),
            provider=str(meta.get("provider") or ""),
            latency_ms=latency_ms,
            segment_index=task.segment_index,
            error="" if ok else str(exc or "empty"),
            difficulty=difficulty,
        )

    def get_result(self, segment_index: int) -> LLMTaskResult | None:
        with self._results_lock:
            return self._results.get(segment_index)

    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return dict(self._stats)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": _orchestrator_enabled(),
            "started": self._started,
            "workers": len(self._workers),
            "queue_depth": self._task_queue.qsize(),
            "stats": self.stats(),
            "pool": self._pool.to_dict(),
            "planner": get_planner().to_dict(),
        }


_orch: LLMOrchestrator | None = None
_orch_lock = threading.Lock()


def get_llm_orchestrator() -> LLMOrchestrator:
    global _orch
    with _orch_lock:
        if _orch is None:
            _orch = LLMOrchestrator()
        return _orch
