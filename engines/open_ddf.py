"""OpenDDF — Open Diagnostic Data Format for TubeDub AI Core 3.x.

Per-task diagnostic records for every agent step in the AutoDub pipeline.
Thread-safe in-memory store with JSON persistence to output/ddf_{task_id}.json.

Usage:
    from engines.open_ddf import open_ddf

    open_ddf.record_agent(task_id, "Whisper/STT", called=True, success=True)
    open_ddf.record_agent(task_id, "Translation", called=True, success=False,
                          error="timeout", fallback_used=True)
    open_ddf.save(task_id)
    report = open_ddf.get_report(task_id)
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.open_ddf")

_APP_DIR: Path = Path(__file__).resolve().parent.parent
_OUTPUT_DIR: Path = _APP_DIR / "output"

_lock = threading.Lock()
_store: dict[str, dict] = {}  # task_id → report dict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_task(task_id: str) -> dict:
    """Return (creating if needed) the report dict for task_id."""
    if task_id not in _store:
        _store[task_id] = {
            "task_id": task_id,
            "created_at": _now_iso(),
            "agents": [],
            "segment_attention": [],
            "summary": {
                "total_agents": 0,
                "failed_agents": 0,
                "fallback_used": 0,
                "warnings": 0,
                "total_execution_time_ms": 0.0,
                "total_llm_calls": 0,
            },
        }
    return _store[task_id]


def record_agent(
    task_id: str,
    agent_name: str,
    *,
    called: bool = True,
    success: bool = True,
    error: str | None = None,
    decision: str | None = None,
    fallback_used: bool = False,
    segment_idx: int | None = None,
    execution_time_ms: float | None = None,
    retry_count: int = 0,
    fallback_reason: str | None = None,
    llm_calls: int = 0,
    input_metrics: dict[str, Any] | None = None,
    output_metrics: dict[str, Any] | None = None,
) -> None:
    """Record one agent invocation into the in-memory DDF store.

    Parameters
    ----------
    task_id:      Pipeline task identifier.
    agent_name:   Human-readable agent name (e.g. "Whisper/STT", "Translation").
    called:       Whether the agent was actually invoked.
    success:      Whether the agent completed without error.
    error:        Error message string if not success.
    decision:     Optional agent decision / note (e.g. "LLM skipped").
    fallback_used: True when a fallback path was taken instead of the primary.
    segment_idx:  Segment index, when recording per-segment failures.
    execution_time_ms: Wall time for the agent step.
    retry_count: Number of retries attempted.
    fallback_reason: Why fallback was used (timeout, llm_circuit_open, …).
    llm_calls: LLM invocations attributed to this agent step.
    input_metrics / output_metrics: Compact summary dicts for the report.
    """
    with _lock:
        report = _ensure_task(task_id)
        entry: dict[str, Any] = {
            "agent_name": agent_name,
            "called": called,
            "success": success,
            "error_msg": error,
            "decision": decision,
            "fallback_used": fallback_used,
            "timestamp": _now_iso(),
            "segment_idx": segment_idx,
            "execution_time_ms": round(float(execution_time_ms or 0.0), 1),
            "retry_count": int(retry_count or 0),
            "fallback_reason": fallback_reason,
            "llm_calls": int(llm_calls or 0),
            "input_metrics": dict(input_metrics or {}),
            "output_metrics": dict(output_metrics or {}),
        }
        report["agents"].append(entry)

        summary = report["summary"]
        summary["total_agents"] = len(report["agents"])
        if execution_time_ms is not None:
            summary["total_execution_time_ms"] = round(
                float(summary.get("total_execution_time_ms") or 0) + float(execution_time_ms),
                1,
            )
        summary["total_llm_calls"] = int(summary.get("total_llm_calls") or 0) + int(llm_calls or 0)
        if not success:
            summary["failed_agents"] = summary.get("failed_agents", 0) + 1
        if fallback_used:
            summary["fallback_used"] = summary.get("fallback_used", 0) + 1
        if error or not success:
            summary["warnings"] = summary.get("warnings", 0) + 1

    if not success:
        logger.warning(
            "[OpenDDF] task=%s agent=%s FAILED error=%s fallback=%s",
            task_id,
            agent_name,
            error,
            fallback_used,
        )
    else:
        logger.debug(
            "[OpenDDF] task=%s agent=%s OK decision=%s",
            task_id,
            agent_name,
            decision,
        )


def mark_segment_attention(
    task_id: str,
    seg_idx: int,
    reason: str,
) -> None:
    """Flag a segment for attention in the DDF report (e.g. tts_failed, fit_skipped)."""
    with _lock:
        report = _ensure_task(task_id)
        report["segment_attention"].append(
            {
                "seg_idx": seg_idx,
                "reason": reason,
                "timestamp": _now_iso(),
            }
        )
    logger.debug(
        "[OpenDDF] task=%s segment=%d attention=%s",
        task_id,
        seg_idx,
        reason,
    )


def get_report(task_id: str) -> dict:
    """Return the full DDF report dict for task_id (empty report if not found)."""
    with _lock:
        if task_id not in _store:
            return {
                "task_id": task_id,
                "error": "no_data",
                "agents": [],
                "segment_attention": [],
                "summary": {},
            }
        return dict(_store[task_id])


def save(task_id: str) -> Path | None:
    """Persist the DDF report to output/ddf_{task_id}.json.

    Returns the path written, or None on error.
    """
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = _OUTPUT_DIR / f"ddf_{task_id}.json"
        with _lock:
            report = dict(_store.get(task_id) or {})
        if not report:
            return None
        report["saved_at"] = _now_iso()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        logger.info("[OpenDDF] Saved report for task %s → %s", task_id, path)
        return path
    except Exception as exc:
        logger.warning("[OpenDDF] Could not save report for task %s: %s", task_id, exc)
        return None


def load(task_id: str) -> dict | None:
    """Load a previously-saved DDF report from disk (for GET endpoint)."""
    path = _OUTPUT_DIR / f"ddf_{task_id}.json"
    if not path.is_file():
        with _lock:
            if task_id in _store:
                return dict(_store[task_id])
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


class _OpenDDFProxy:
    """Module-level singleton proxy so callers use `open_ddf.record_agent(...)` style."""

    def record_agent(self, task_id: str, agent_name: str, **kwargs: Any) -> None:
        try:
            record_agent(task_id, agent_name, **kwargs)
        except Exception as exc:
            logger.debug("[OpenDDF] record_agent error (suppressed): %s", exc)

    def get_report(self, task_id: str) -> dict:
        try:
            return get_report(task_id)
        except Exception:
            return {}

    def save(self, task_id: str) -> Path | None:
        try:
            return save(task_id)
        except Exception:
            return None

    def load(self, task_id: str) -> dict | None:
        try:
            return load(task_id)
        except Exception:
            return None

    def mark_segment_attention(self, task_id: str, seg_idx: int, reason: str) -> None:
        try:
            mark_segment_attention(task_id, seg_idx, reason)
        except Exception:
            pass


open_ddf = _OpenDDFProxy()

__all__ = ["open_ddf", "record_agent", "get_report", "save", "load", "mark_segment_attention"]
