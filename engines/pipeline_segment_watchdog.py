"""Per-segment pipeline watchdog — structured logging and bounded wait."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

logger = logging.getLogger("tubedub.pipeline_segment_watchdog")

SEGMENT_PROCESS_TIMEOUT_SEC = 30.0

T = TypeVar("T")


@dataclass
class SegmentWatchResult:
    value: T
    timed_out: bool = False
    elapsed_sec: float = 0.0
    attempts: int = 1
    error: str = ""


def current_thread_id() -> int:
    return threading.get_ident()


def log_segment_start(
    task_id: str,
    phase: str,
    segment_index: int,
    *,
    stage: str | None = None,
) -> None:
    stage_label = stage or phase
    logger.info(
        "START SEGMENT %d task=%s phase=%s stage=%s thread=%d",
        segment_index,
        task_id or "?",
        phase,
        stage_label,
        current_thread_id(),
    )


def log_segment_end(
    task_id: str,
    phase: str,
    segment_index: int,
    elapsed_sec: float,
    *,
    stage: str | None = None,
    error: str = "",
) -> None:
    stage_label = stage or phase
    err_suffix = f" error={error}" if error else ""
    logger.info(
        "END SEGMENT %d task=%s phase=%s stage=%s elapsed=%.2fs thread=%d%s",
        segment_index,
        task_id or "?",
        phase,
        stage_label,
        elapsed_sec,
        current_thread_id(),
        err_suffix,
    )


def run_segment_bounded(
    *,
    task_id: str,
    phase: str,
    segment_index: int,
    fn: Callable[[], T],
    fallback: Callable[[], T],
    timeout_sec: float = SEGMENT_PROCESS_TIMEOUT_SEC,
    attempt: int = 1,
    stage: str | None = None,
) -> SegmentWatchResult:
    """
    Run segment work with a HARD wall-clock limit (P0: the pipeline can never
    hang on a slow segment).

    The work runs on a daemon thread. We wait at most ``timeout_sec`` for it via
    an :class:`threading.Event`; on timeout we return the ``fallback`` value
    IMMEDIATELY and never block on the still-running worker. This is critical:
    Python threads cannot be force-killed, and a slow local LLM call may run for
    minutes. The previous implementation used ``with ThreadPoolExecutor(...) as
    pool``; when ``result(timeout=...)`` fired, the ``with`` block's
    ``shutdown(wait=True)`` still blocked until the orphaned worker finished, so
    the "timeout" waited for the underlying LLM call (observed: a 30s watchdog
    actually taking 114s). Using a detached daemon thread guarantees the bound.

    The orphaned worker is a daemon and finishes on its own once its inner
    (finite-timeout) call returns, then dies; it never blocks interpreter exit
    and never prevents the pipeline from advancing.

    On timeout or error: log, return the fallback value, caller continues.
    """
    stage_label = stage or phase
    t0 = time.perf_counter()
    log_segment_start(task_id, phase, segment_index, stage=stage_label)
    if attempt > 1:
        logger.info(
            "[SegmentWatch] task=%s phase=%s segment=%d attempt=%d",
            task_id or "?",
            phase,
            segment_index,
            attempt,
        )

    try:
        wait_for = float(timeout_sec)
    except (TypeError, ValueError):
        wait_for = SEGMENT_PROCESS_TIMEOUT_SEC
    if wait_for <= 0:
        wait_for = SEGMENT_PROCESS_TIMEOUT_SEC

    result_box: dict[str, object] = {}
    done = threading.Event()

    def _runner() -> None:
        try:
            result_box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — surface every failure
            result_box["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(
        target=_runner,
        name=f"segwatch-{phase}-{segment_index}",
        daemon=True,
    )
    worker.start()
    finished = done.wait(wait_for)
    elapsed = time.perf_counter() - t0

    if not finished:
        # HARD timeout: do NOT wait for the orphaned worker (daemon) — it would
        # defeat the wall-clock bound. Return the safe fallback and move on.
        logger.warning(
            "[SegmentWatch] task=%s phase=%s segment=%d TIMEOUT after %.2fs "
            "attempt=%d — finishing segment with fallback",
            task_id or "?",
            phase,
            segment_index,
            elapsed,
            attempt,
        )
        log_segment_end(
            task_id,
            phase,
            segment_index,
            elapsed,
            stage=stage_label,
            error=f"timeout_{timeout_sec}s",
        )
        return SegmentWatchResult(
            value=fallback(),
            timed_out=True,
            elapsed_sec=elapsed,
            attempts=attempt,
            error=f"timeout_{timeout_sec}s",
        )

    if "error" in result_box:
        exc = result_box["error"]
        logger.error(
            "[SegmentWatch] task=%s phase=%s segment=%d failed after %.2fs attempt=%d: %s",
            task_id or "?",
            phase,
            segment_index,
            elapsed,
            attempt,
            exc,
            exc_info=exc if isinstance(exc, BaseException) else None,
        )
        log_segment_end(
            task_id,
            phase,
            segment_index,
            elapsed,
            stage=stage_label,
            error=str(exc),
        )
        return SegmentWatchResult(
            value=fallback(),
            timed_out=False,
            elapsed_sec=elapsed,
            attempts=attempt,
            error=str(exc),
        )

    log_segment_end(task_id, phase, segment_index, elapsed, stage=stage_label)
    return SegmentWatchResult(
        value=result_box.get("value"), elapsed_sec=elapsed, attempts=attempt
    )
