"""Launch Decision Trace — per-stage + per-agent ledger for the
Meaning-First Pipeline launch path.

Purpose
-------
The failing AutoDub scenario proved that we cannot rely on aggregate
"segment_count" / "not_called" indicators to know why the pipeline died
before Semantic V3 ever received inputs. The Launch Decision Trace is a
dedicated ledger that:

1. Records SUCCESS / FAILED / SKIPPED for every launch-critical stage
   (`Video Loaded`, `Audio Extracted`, `STT Started`, `Words Built`,
   `Sentence Builder`, `Meaning Pipeline`, `Variant Generator`,
   `Duration Predictor`, `Meaning Fit`, `Translation`, `Adaptation`,
   `TTS`, `Timeline`, `Scheduler`, `Render`).
2. Records an AI-agent invocation ledger (Planner, Translation,
   Semantic, Entity, Timing, Grammar, Quality, Voice, Mix) as
   `called_by=<module:line>` or `skipped_reason=<explicit reason>`.
3. Emits every record BOTH to the runtime NDJSON debug log
   (``debug-7e57dc.log``) AND into ``task["info"]["launch_decision_trace"]``
   so studio / OpenDDF can pick it up.

Contract enforced
-----------------
* ``status`` MUST be one of ``SUCCESS`` / ``FAILED`` / ``SKIPPED``.
* ``reason`` MUST be a non-empty explicit string. The literal string
  ``not_called`` is FORBIDDEN as a reason (raises ``ValueError`` at
  emission time). The pipeline is required to name a decision-taker or
  an explicit skip reason for every agent slot.
* The trace is best-effort persistent: log-writer / task-info-writer
  failures are swallowed defensively so the trace never breaks the run,
  but every failure is itself logged via the module logger.

The module is intentionally tiny and dependency-free (stdlib only) so
it can be imported from anywhere in the launch path without pulling in
Semantic V3, translation, dub-engine or scheduler modules.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger("tubedub.launch_decision_trace")

# ── Constants ─────────────────────────────────────────────────────────

SESSION_ID = "7e57dc"
RUN_ID = "meaning-first-launch"

# The debug log path mirrors the one used by the rest of the debug-mode
# NDJSON emitters (auto_dub_api / phase2 / meaning_first_pipeline).
_APP_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_DEBUG_LOG_PATH = _APP_DIR / "debug-7e57dc.log"

# Stages in exact TZ order — used to seed placeholder SKIPPED rows so
# gaps in the pipeline are visible even when a stage silently returned
# early upstream.
LAUNCH_STAGES: tuple[str, ...] = (
    "Video Loaded",
    "Audio Extracted",
    "STT Started",
    "Words Built",
    "Sentence Builder",
    "Meaning Pipeline",
    "Variant Generator",
    "Duration Predictor",
    "Meaning Fit",
    "Translation",
    "Adaptation",
    "TTS",
    "Timeline",
    "Scheduler",
    "Render",
)

# AI-agent slots. Names match `ai_core_report.ai_agent_report.agent_order`.
AI_AGENT_SLOTS: tuple[str, ...] = (
    "planner",
    "translation",
    "semantic",
    "entity",
    "timing",
    "grammar",
    "quality",
    "voice",
    "mix",
)

_ALLOWED_STATUSES = frozenset({"SUCCESS", "FAILED", "SKIPPED"})
_FORBIDDEN_REASONS = frozenset({"not_called", "", None})

_LOCK = threading.Lock()


# ── Formatting helpers ────────────────────────────────────────────────

def _module_line(module: str | None, line: int | None) -> str:
    """Return ``file:line`` locator, tolerant of missing pieces."""
    mod = module or "unknown"
    if line is None or line < 0:
        return mod
    return f"{mod}:{int(line)}"


def _validate_status(status: str) -> str:
    up = str(status).upper()
    if up not in _ALLOWED_STATUSES:
        raise ValueError(
            f"launch_decision_trace: invalid status={status!r}; "
            f"expected one of {sorted(_ALLOWED_STATUSES)}"
        )
    return up


def _validate_reason(reason: Any) -> str:
    r = (str(reason) if reason is not None else "").strip()
    if r in _FORBIDDEN_REASONS or r.lower() == "not_called":
        raise ValueError(
            "launch_decision_trace: reason must be an explicit non-empty "
            "string and cannot be literally 'not_called' — supply a "
            "concrete skip reason (e.g. 'feature_flag_off', "
            "'no_source_segments', 'exception:<Type>')."
        )
    return r


# ── Persistence primitives ────────────────────────────────────────────

def _write_ndjson(payload: Mapping[str, Any], log_path: Path | None = None) -> None:
    """Append a single NDJSON record to the runtime debug log.

    Best effort: OSError is logged but never re-raised (so tracing can
    never break the pipeline it's diagnosing).
    """
    path = Path(log_path) if log_path is not None else _DEFAULT_DEBUG_LOG_PATH
    line = json.dumps(payload, ensure_ascii=False, sort_keys=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:  # pragma: no cover - defensive
        logger.debug("launch_decision_trace: NDJSON write skipped: %s", exc)


def _get_trace_bucket(task_info: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the ``launch_decision_trace`` dict inside task_info, creating it if needed."""
    if task_info is None:
        return None
    bucket = task_info.get("launch_decision_trace")
    if not isinstance(bucket, dict):
        bucket = {
            "session_id": SESSION_ID,
            "run_id": RUN_ID,
            "created_at_ms": int(time.time() * 1000),
            "stages": [],
            "agents": {},
        }
        task_info["launch_decision_trace"] = bucket
    else:
        bucket.setdefault("stages", [])
        bucket.setdefault("agents", {})
    return bucket


# ── Public API ────────────────────────────────────────────────────────

def record_stage(
    stage: str,
    *,
    status: str,
    reason: str,
    module: str = "",
    line: int | None = None,
    data: Mapping[str, Any] | None = None,
    task_info: dict[str, Any] | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Record a stage decision.

    Args:
        stage: One of :data:`LAUNCH_STAGES` — unknown stages are still
            emitted (to help catch typos) but are logged at debug.
        status: One of ``SUCCESS`` / ``FAILED`` / ``SKIPPED``.
        reason: Explicit non-empty reason. The literal string
            ``not_called`` is FORBIDDEN.
        module: Module locator (typically ``"api/auto_dub_api.py"``).
        line: Optional source line inside ``module``.
        data: Optional non-content metrics (e.g. counters, flag values).
        task_info: If provided, the record is appended to
            ``task_info['launch_decision_trace']['stages']``.
        log_path: Optional override for the NDJSON file (used by tests).

    Returns:
        The full payload that was emitted (useful for tests).
    """

    validated_status = _validate_status(status)
    validated_reason = _validate_reason(reason)

    if stage not in LAUNCH_STAGES:
        logger.debug("launch_decision_trace: unknown stage=%r", stage)

    payload: dict[str, Any] = {
        "sessionId": SESSION_ID,
        "runId": RUN_ID,
        "kind": "stage",
        "stage": str(stage),
        "status": validated_status,
        "reason": validated_reason,
        "location": _module_line(module, line),
        "data": dict(data or {}),
        "timestamp": int(time.time() * 1000),
    }
    _write_ndjson(payload, log_path=log_path)

    bucket = _get_trace_bucket(task_info)
    if bucket is not None:
        bucket["stages"].append(dict(payload))
    return payload


def record_agent(
    agent: str,
    *,
    called: bool,
    called_by: str = "",
    skipped_reason: str = "",
    module: str = "",
    line: int | None = None,
    data: Mapping[str, Any] | None = None,
    task_info: dict[str, Any] | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Record an AI-agent invocation ledger entry.

    Contract:
        * ``called=True`` requires either an explicit ``called_by`` or a
          non-empty ``module`` — the record must name a decision-taker.
        * ``called=False`` requires an explicit ``skipped_reason`` other
          than ``not_called``.
    """

    kind_locator = _module_line(module, line)

    if called:
        decision_taker = called_by.strip() or kind_locator
        if not decision_taker or decision_taker == "unknown":
            raise ValueError(
                f"launch_decision_trace: agent {agent!r} marked called=True "
                "requires an explicit called_by / module locator; "
                "'not_called' style records are forbidden."
            )
        payload: dict[str, Any] = {
            "sessionId": SESSION_ID,
            "runId": RUN_ID,
            "kind": "agent",
            "agent": str(agent),
            "status": "CALLED",
            "called_by": decision_taker,
            "data": dict(data or {}),
            "timestamp": int(time.time() * 1000),
        }
    else:
        _validate_reason(skipped_reason)
        payload = {
            "sessionId": SESSION_ID,
            "runId": RUN_ID,
            "kind": "agent",
            "agent": str(agent),
            "status": "SKIPPED",
            "skipped_reason": str(skipped_reason).strip(),
            "location": kind_locator,
            "data": dict(data or {}),
            "timestamp": int(time.time() * 1000),
        }

    _write_ndjson(payload, log_path=log_path)

    bucket = _get_trace_bucket(task_info)
    if bucket is not None:
        bucket["agents"][str(agent)] = dict(payload)
    return payload


def seed_ai_agent_slots(
    task_info: dict[str, Any] | None,
    *,
    default_reason: str = "pending_upstream_stage",
    module: str = "engines/semantic_v3/launch_decision_trace.py",
    line: int | None = None,
) -> None:
    """Populate ``task_info['launch_decision_trace']['agents']`` with an
    explicit placeholder for every :data:`AI_AGENT_SLOTS`.

    This guarantees that the trace never contains a silent gap — before
    any agent runs, each slot is stamped with a concrete
    ``skipped_reason`` (default ``pending_upstream_stage``). Later
    ``record_agent`` calls overwrite the placeholder with the real
    ``called_by`` / ``skipped_reason``.

    The literal reason ``not_called`` is deliberately impossible to
    produce here — the validation in :func:`record_agent` rejects it.
    """
    if task_info is None:
        return
    bucket = _get_trace_bucket(task_info)
    if bucket is None:
        return
    for slot in AI_AGENT_SLOTS:
        if slot in bucket["agents"]:
            continue
        record_agent(
            slot,
            called=False,
            skipped_reason=default_reason,
            module=module,
            line=line,
            task_info=task_info,
        )


def summarize(task_info: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact summary usable by studio / OpenDDF surfaces."""
    if task_info is None:
        return {"stages": [], "agents": {}, "session_id": SESSION_ID}
    bucket = _get_trace_bucket(task_info) or {}
    stages: Iterable[dict[str, Any]] = bucket.get("stages") or ()
    agents: dict[str, Any] = bucket.get("agents") or {}
    counts = {"SUCCESS": 0, "FAILED": 0, "SKIPPED": 0}
    for row in stages:
        s = str(row.get("status") or "").upper()
        if s in counts:
            counts[s] += 1
    return {
        "session_id": bucket.get("session_id", SESSION_ID),
        "run_id": bucket.get("run_id", RUN_ID),
        "stage_counts": counts,
        "stages": list(stages),
        "agents": dict(agents),
    }


def is_debug_ingest_enabled() -> bool:
    """True when the user opted into the HTTP ingest endpoint.

    The trace never contacts the ingest server directly — that is left
    to the debug UI. This helper is exposed so callers can attach the
    trace payload to their own HTTP requests when they choose to.
    """
    val = str(os.environ.get("VM_DEBUG_INGEST", "")).strip().lower()
    return val in ("1", "true", "yes", "on")


def fail_stt_zero_segments(
    *,
    task_info: dict[str, Any] | None = None,
    raw_count: int = 0,
    module: str = "api/auto_dub_api.py",
    line: int | None = None,
    log_path: Path | None = None,
) -> None:
    """Hard-fail the STT → Words Built boundary when segment_count=0.

    Emits explicit Decision Trace records then raises
    :class:`~engines.pipeline_integrity.exceptions.ArchitectureViolation`
    — never a silent ``pipeline_critical`` termination.
    """
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    record_stage(
        "STT Started",
        status="FAILED",
        reason="stt_completed_zero_segments",
        module=module,
        line=line,
        data={"raw_count": raw_count, "merged_count": 0},
        task_info=task_info,
        log_path=log_path,
    )
    record_stage(
        "Words Built",
        status="FAILED",
        reason="stt_zero_segments_no_handoff",
        module=module,
        line=line,
        data={"segment_count": 0},
        task_info=task_info,
        log_path=log_path,
    )
    record_stage(
        "Meaning Pipeline",
        status="SKIPPED",
        reason="upstream_stt_zero_segments",
        module=module,
        line=line,
        task_info=task_info,
        log_path=log_path,
    )
    raise ArchitectureViolation(
        "STT handoff produced zero segments — Meaning-First Pipeline cannot start",
        stage="STT",
        rule="stt_segment_count_min_1",
        details={"raw_count": raw_count, "merged_count": 0},
    )


__all__ = [
    "AI_AGENT_SLOTS",
    "LAUNCH_STAGES",
    "RUN_ID",
    "SESSION_ID",
    "fail_stt_zero_segments",
    "is_debug_ingest_enabled",
    "record_agent",
    "record_stage",
    "seed_ai_agent_slots",
    "summarize",
]
