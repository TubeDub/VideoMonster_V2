"""Decision Trace — full adaptation chain with mandatory terminal status.

Every stage must end as SUCCESS | FAILED | SKIPPED(reason).
Silent / empty outcomes are illegal.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("tubedub.dub_engine.decision_trace")

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"
TERMINAL_STATUSES = frozenset({STATUS_SUCCESS, STATUS_FAILED, STATUS_SKIPPED})

# Canonical ordered stages (OpenDDF Decision Trace)
STAGE_NEED_ADAPTATION = "need_adaptation"
STAGE_DECISION_ENGINE = "decision_engine"
STAGE_RULE_ADAPTER = "rule_adapter"
STAGE_SEMANTIC_ADAPTER = "semantic_adapter"
STAGE_CHOSEN_STRATEGY = "chosen_strategy"
STAGE_STRATEGY_RESULT = "strategy_result"
STAGE_TTS = "tts_duration"
STAGE_SCHEDULER = "scheduler"
STAGE_FINAL = "final_result"

CANONICAL_STAGES = (
    STAGE_NEED_ADAPTATION,
    STAGE_DECISION_ENGINE,
    STAGE_RULE_ADAPTER,
    STAGE_SEMANTIC_ADAPTER,
    STAGE_CHOSEN_STRATEGY,
    STAGE_STRATEGY_RESULT,
    STAGE_TTS,
    STAGE_SCHEDULER,
    STAGE_FINAL,
)


def _dbg(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "ee98a6",
            "runId": "decision-trace",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(
            r"c:\Users\serhii\Desktop\VideoMonster_V2\debug-ee98a6.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


def _trace_list(seg: dict[str, Any]) -> list[dict[str, Any]]:
    raw = seg.get("decision_trace")
    if not isinstance(raw, list):
        raw = []
        seg["decision_trace"] = raw
    return raw


def record_stage(
    seg: dict[str, Any],
    *,
    stage: str,
    status: str,
    reason: str = "",
    detail: dict[str, Any] | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """Append one Decision Trace stage. SKIPPED requires non-empty reason."""
    st = str(status or "").upper().strip()
    if st not in TERMINAL_STATUSES:
        st = STATUS_FAILED
        reason = reason or "InvalidStatus"
    if st == STATUS_SKIPPED and not str(reason or "").strip():
        reason = "UnknownSkip"
    entry = {
        "stage": str(stage),
        "status": st,
        "reason": str(reason or ""),
        "detail": dict(detail or {}),
        "ts_ms": int(time.time() * 1000),
    }
    if index is not None:
        entry["index"] = int(index)
    stages = _trace_list(seg)
    # Replace last entry with same stage name (idempotent update) else append
    replaced = False
    for i in range(len(stages) - 1, -1, -1):
        if stages[i].get("stage") == entry["stage"]:
            stages[i] = entry
            replaced = True
            break
    if not replaced:
        stages.append(entry)

    # Human transition line for logs / OpenDDF
    transitions = seg.setdefault("decision_transitions", [])
    if not isinstance(transitions, list):
        transitions = []
        seg["decision_transitions"] = transitions
    line = f"{entry['stage']} = {entry['status']}"
    if entry["reason"]:
        line += f" ({entry['reason']})"
    for k, v in (detail or {}).items():
        if k in ("overflow_ms", "underflow_ms", "duration_ms", "new_duration_ms", "chosen"):
            line += f" | {k}={v}"
    transitions.append(line)

    logger.info(
        "DECISION_TRACE seg=%s stage=%s status=%s reason=%s detail=%s",
        seg.get("segment_id") or index,
        entry["stage"],
        entry["status"],
        entry["reason"],
        entry["detail"],
    )
    _dbg(
        "A",
        "decision_trace.py:record_stage",
        "stage_recorded",
        {
            "segment_id": str(seg.get("segment_id") or ""),
            "stage": entry["stage"],
            "status": entry["status"],
            "reason": entry["reason"],
            "detail": entry["detail"],
        },
    )
    return entry


def record_need_adaptation(
    seg: dict[str, Any],
    *,
    need: bool,
    overflow_ms: int = 0,
    underflow_ms: int = 0,
    index: int = 0,
) -> None:
    record_stage(
        seg,
        stage=STAGE_NEED_ADAPTATION,
        status=STATUS_SUCCESS if need else STATUS_SKIPPED,
        reason="" if need else "FitsNoChange",
        detail={
            "need_adaptation": need,
            "overflow_ms": int(overflow_ms),
            "underflow_ms": int(underflow_ms),
        },
        index=index,
    )


def record_strategy_choice(
    seg: dict[str, Any],
    *,
    chosen: str,
    why: str = "",
    cost: float | None = None,
    overflow_ms: int = 0,
    index: int = 0,
) -> None:
    record_stage(
        seg,
        stage=STAGE_DECISION_ENGINE,
        status=STATUS_SUCCESS,
        reason=why or f"chosen={chosen}",
        detail={
            "chosen": chosen,
            "why": why,
            "cost": cost,
            "overflow_ms": int(overflow_ms),
        },
        index=index,
    )
    record_stage(
        seg,
        stage=STAGE_CHOSEN_STRATEGY,
        status=STATUS_SUCCESS,
        reason=why or f"min_cost_strategy={chosen}",
        detail={"chosen": chosen, "cost": cost, "overflow_ms": int(overflow_ms)},
        index=index,
    )


def record_adapter_outcome(
    seg: dict[str, Any],
    *,
    adapter: str,
    status: str,
    reason: str = "",
    detail: dict[str, Any] | None = None,
    index: int = 0,
) -> None:
    stage = (
        STAGE_RULE_ADAPTER
        if "rule" in adapter.lower()
        else STAGE_SEMANTIC_ADAPTER
        if "semantic" in adapter.lower() or "dsal" in adapter.lower()
        else adapter
    )
    record_stage(
        seg,
        stage=stage,
        status=status,
        reason=reason,
        detail=detail or {},
        index=index,
    )


def record_final_result(
    seg: dict[str, Any],
    *,
    status: str,
    reason: str = "",
    overflow_ms: int = 0,
    duration_ms: int = 0,
    index: int = 0,
) -> None:
    record_stage(
        seg,
        stage=STAGE_FINAL,
        status=status,
        reason=reason,
        detail={"overflow_ms": int(overflow_ms), "duration_ms": int(duration_ms)},
        index=index,
    )


def find_silent_stages(seg: dict[str, Any]) -> list[str]:
    """Return stage names present without a terminal status (should be empty)."""
    silent: list[str] = []
    for entry in _trace_list(seg):
        if not isinstance(entry, dict):
            silent.append("malformed")
            continue
        st = str(entry.get("status") or "")
        if st not in TERMINAL_STATUSES:
            silent.append(str(entry.get("stage") or "?"))
        if st == STATUS_SKIPPED and not str(entry.get("reason") or "").strip():
            silent.append(f"{entry.get('stage')}:skip_without_reason")
    return silent


def ensure_decision_trace_complete(
    seg: dict[str, Any],
    *,
    index: int = 0,
) -> dict[str, Any]:
    """
    Before OpenDDF / SUCCESS gate: ensure decision_trace exists and has no silent stages.
    Fills minimal chain from adaptation_decision if empty.
    """
    from engines.dub_engine_v2.adaptation_decision import ensure_skip_reason

    snap = dict(seg.get("adaptation_decision") or {})
    ov = int(seg.get("overflow_ms") or snap.get("overflow_ms") or 0)
    und = int(seg.get("underflow_ms") or snap.get("underflow_ms") or 0)
    need = bool(snap.get("need_adaptation")) or ov > 0 or und > 0
    executed = bool(seg.get("adaptation_executed"))
    skip = ensure_skip_reason(seg, index=index) if not executed else ""

    stages = _trace_list(seg)
    if not stages:
        # Reconstruct minimal Decision Trace from known fields (hypothesis A/B)
        record_need_adaptation(
            seg, need=need, overflow_ms=ov, underflow_ms=und, index=index
        )
        locked = bool(snap.get("translation_locked") or seg.get("translation_locked"))
        if locked and need and not executed:
            record_adapter_outcome(
                seg,
                adapter="rule",
                status=STATUS_SKIPPED,
                reason=skip or "TranslationLocked",
                index=index,
            )
            record_adapter_outcome(
                seg,
                adapter="semantic",
                status=STATUS_SKIPPED,
                reason=skip or "TranslationLocked",
                index=index,
            )
        elif executed:
            chosen = str(
                snap.get("decision")
                or (seg.get("overflow_decision") or {}).get("chosen")
                or "adapted"
            )
            why = str((seg.get("overflow_decision") or {}).get("why") or "")
            record_strategy_choice(
                seg, chosen=chosen, why=why, overflow_ms=ov, index=index
            )
            record_stage(
                seg,
                stage=STAGE_STRATEGY_RESULT,
                status=STATUS_SUCCESS,
                reason=why or chosen,
                detail={"chosen": chosen, "overflow_ms": ov},
                index=index,
            )
        else:
            record_stage(
                seg,
                stage=STAGE_DECISION_ENGINE,
                status=STATUS_SKIPPED,
                reason=skip or "UnknownSkip",
                detail={"overflow_ms": ov},
                index=index,
            )
        # Final
        if ov > 0 and not executed:
            record_final_result(
                seg,
                status=STATUS_FAILED,
                reason=f"OverflowDetected_AdaptationSkipped:{skip}",
                overflow_ms=ov,
                index=index,
            )
        elif executed or not need:
            record_final_result(
                seg,
                status=STATUS_SUCCESS,
                reason="adapted" if executed else "fits",
                overflow_ms=ov,
                index=index,
            )
        else:
            record_final_result(
                seg,
                status=STATUS_SKIPPED,
                reason=skip or "UnknownSkip",
                overflow_ms=ov,
                index=index,
            )

    silent = find_silent_stages(seg)
    report = {
        "stages": list(seg.get("decision_trace") or []),
        "transitions": list(seg.get("decision_transitions") or []),
        "silent_stages": silent,
        "ok": not silent,
    }
    seg["decision_trace_report"] = report
    _dbg(
        "B",
        "decision_trace.py:ensure_complete",
        "trace_finalized",
        {
            "segment_id": str(seg.get("segment_id") or ""),
            "stage_count": len(report["stages"]),
            "silent": silent,
            "executed": executed,
            "overflow_ms": ov,
            "skip_reason": skip,
        },
    )
    return report


def format_decision_trace_openddf(seg: dict[str, Any]) -> dict[str, Any]:
    """OpenDDF Decision Trace block for one segment."""
    ensure_decision_trace_complete(seg)
    stages = list(seg.get("decision_trace") or [])
    numbered = []
    for i, s in enumerate(stages, start=1):
        numbered.append(
            {
                "stage_index": i,
                "name": s.get("stage"),
                "status": s.get("status"),
                "reason": s.get("reason") or "",
                "detail": s.get("detail") or {},
            }
        )
    return {
        "title": "Decision Trace",
        "stages": numbered,
        "transitions": list(seg.get("decision_transitions") or []),
        "summary": " → ".join(
            f"{s.get('name')}={s.get('status')}"
            + (f"({s.get('reason')})" if s.get("reason") else "")
            for s in numbered
        ),
    }


def assert_no_silent_decision_stages(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return segments that still have silent / incomplete decision stages."""
    bad: list[dict[str, Any]] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        rep = ensure_decision_trace_complete(seg, index=i)
        if rep.get("silent_stages"):
            bad.append(
                {
                    "index": i,
                    "segment_id": seg.get("segment_id"),
                    "silent_stages": rep["silent_stages"],
                }
            )
    _dbg(
        "D",
        "decision_trace.py:assert_no_silent",
        "silent_scan",
        {"bad_count": len(bad), "sample": bad[:3]},
    )
    return bad
