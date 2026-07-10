"""AI Core 4.2 — streaming Voice stage (per-segment TTS conveyor / pool)."""

from __future__ import annotations

import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.streaming_pipeline.mode import AI_CORE_VERSION_STREAMING

logger = logging.getLogger("tubedub.ai_core.streaming_voice")

SegmentTtsHandler = Callable[
    [int, int, dict[str, Any], dict[str, Any], dict[str, Any], str],
    dict[str, Any],
]

DEFAULT_VOICE_WORKERS = 4


def process_voice_segment(
    list_index: int,
    seg: dict[str, Any],
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    handler: SegmentTtsHandler | None = None,
) -> dict[str, Any]:
    """Synthesize one segment — isolated failure does not affect others."""
    fn = handler or state.get("segment_tts_handler")
    if not fn:
        return seg
    segment_index = int(seg.get("index", list_index))
    try:
        return fn(list_index, segment_index, seg, manifest, state, task_id)
    except Exception as exc:
        logger.warning("Streaming voice seg=%s failed: %s", segment_index, exc)
        out = copy.deepcopy(seg)
        from engines.pipeline_integrity.tts_segment_fields import apply_tts_synthesis_result

        apply_tts_synthesis_result(
            out,
            tts_text=str(out.get("tts_text") or out.get("text") or ""),
            tts_file_path=None,
            status="failed",
        )
        return out


class StreamingVoicePool:
    """Parallel TTS workers (thread pool)."""

    def __init__(self, workers: int = DEFAULT_VOICE_WORKERS):
        self.workers = max(1, workers)
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    def submit(self, fn, *args, **kwargs):
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.workers,
                    thread_name_prefix="stream-voice",
                )
            return self._executor.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        with self._lock:
            if self._executor:
                self._executor.shutdown(wait=True)
                self._executor = None


class StreamingVoicePipeline:
    """Parallel per-segment TTS after voice_preparation (skips duplicate batch TTS)."""

    VERSION = AI_CORE_VERSION_STREAMING

    def __init__(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
        *,
        app_dir=None,
    ):
        self.manifest = manifest
        self.state = state
        self.task_id = task_id
        self.app_dir = app_dir
        raw = list(state.get("segments") or [])
        self.segments: list[dict[str, Any]] = [copy.deepcopy(s) for s in raw]
        self.n = len(self.segments)
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._synthesized = 0
        self._failed = 0

    def run(self) -> AgentExecutionResult:
        t0 = time.perf_counter()
        if self.n == 0 or not self.state.get("segment_tts_handler"):
            return self._empty_result(t0, reason="no_handler_or_segments")

        workers = int(self.state.get("streaming_voice_workers") or DEFAULT_VOICE_WORKERS)
        pool = StreamingVoicePool(workers=workers)
        lock = threading.Lock()

        def _work(list_index: int) -> tuple[int, dict[str, Any]]:
            seg = self.segments[list_index]
            updated = process_voice_segment(
                list_index,
                seg,
                manifest=self.manifest,
                state=self.state,
                task_id=self.task_id,
            )
            return list_index, updated

        futures = [pool.submit(_work, i) for i in range(self.n)]
        for fut in as_completed(futures):
            try:
                list_index, updated = fut.result()
                with lock:
                    self.segments[list_index] = updated
                    if updated.get("status") == "failed":
                        self._failed += 1
                        self.warnings.append(f"segment_{list_index}:tts_error")
                    elif updated.get("tts_file_path"):
                        self._synthesized += 1
            except Exception as exc:
                self.errors.append(str(exc))
                logger.warning("Streaming voice future failed: %s", exc)
        pool.shutdown()

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        self.state["segments"] = self.segments
        self.state["streaming_voice_done"] = True
        self.state["voice_agent_path"] = True
        self.state["voice_agent_status"] = "success" if self._failed == 0 else "warning"

        status = "success"
        if self._failed and self._synthesized:
            status = "warning"
        elif self._failed and not self._synthesized:
            status = "warning"

        return AgentExecutionResult(
            status=status,
            updated_state=dict(self.state),
            metrics={
                "pipeline_mode": "streaming",
                "streaming_voice": True,
                "tts_segments_done": self._synthesized,
                "tts_segments_failed": self._failed,
                "execution_time_ms": elapsed,
                "voice_workers": workers,
            },
            warnings=self.warnings,
            errors=self.errors,
            execution_time_ms=elapsed,
            decision_log=[f"streaming_voice_pool workers={workers}"],
        )

    def _empty_result(self, t0: float, *, reason: str) -> AgentExecutionResult:
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        return AgentExecutionResult(
            status="warning",
            updated_state=dict(self.state),
            metrics={"streaming_voice": False, "reason": reason},
            warnings=[reason],
            errors=[],
            execution_time_ms=elapsed,
            decision_log=[reason],
        )
