"""Timing lifecycle audit dump — diagnostics only, no business-logic changes.

Writes NDJSON to debug-ee98a6.log and a JSON snapshot under _tmp_timing_audit/.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.timing_lifecycle_audit")

_DEBUG_LOG = Path(r"c:\Users\serhii\Desktop\VideoMonster_V2\debug-ee98a6.log")
_OUT_DIR = Path(r"c:\Users\serhii\Desktop\VideoMonster_V2\_tmp_timing_audit")


def _append_debug(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "ee98a6",
            "runId": "timing-lifecycle-audit",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return default


def snapshot_segment_timing(
    seg: dict[str, Any],
    *,
    index: int,
    stage: str,
    timing_map_start: int | None = None,
    timing_map_end: int | None = None,
) -> dict[str, Any]:
    """One-segment timing snapshot for audit stages."""
    start_ms = _i(seg.get("start_ms") or seg.get("start_time_ms"))
    end_ms = _i(seg.get("end_ms") or seg.get("end_time_ms"))
    tts_ms = _i(
        seg.get("fitted_ms")
        or seg.get("playback_duration")
        or seg.get("tts_ms")
        or seg.get("actual_duration_ms")
    )
    slot_ms = _i(seg.get("slot_ms")) or max(0, end_ms - start_ms)
    orig = _i(seg.get("original_duration_ms"))
    ov = _i(seg.get("overflow_ms"))
    snap = {
        "stage": stage,
        "index": index,
        "segment_uuid": str(seg.get("segment_id") or seg.get("segment_uuid") or ""),
        "translation_uuid": str(seg.get("translation_uuid") or ""),
        "tts_uuid": str(seg.get("tts_uuid") or ""),
        "audio_uuid": str(seg.get("audio_uuid") or ""),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "slot_ms": slot_ms,
        "timing_map_start_ms": timing_map_start,
        "timing_map_end_ms": timing_map_end,
        "original_duration_ms": orig,
        "predicted_duration_ms": _i(seg.get("predicted_duration_ms")),
        "tts_duration_ms": _i(seg.get("tts_ms") or seg.get("playback_duration")),
        "fitted_ms": _i(seg.get("fitted_ms")),
        "actual_duration_ms": _i(seg.get("actual_duration_ms")),
        "adapted_duration_ms": tts_ms,
        "scheduler_duration_ms": max(0, end_ms - start_ms),
        "render_clip_start": _i(seg.get("merge_adjusted_start") or start_ms),
        "render_clip_end": end_ms,
        "real_audio_end_ms": (_i(seg.get("merge_adjusted_start") or start_ms) + tts_ms)
        if tts_ms
        else None,
        "overflow_ms": ov,
        "underflow_ms": _i(seg.get("underflow_ms")),
        "adaptation_status": seg.get("adaptation_status")
        or (
            "ADAPTATION EXECUTED"
            if seg.get("adaptation_executed")
            else "ADAPTATION NOT EXECUTED"
        ),
        "adaptation_executed": bool(seg.get("adaptation_executed")),
        "decision": str(
            (seg.get("adaptation_decision") or {}).get("decision")
            or (seg.get("overflow_decision") or {}).get("chosen")
            or ""
        ),
        "video_adapt_mode": seg.get("video_adapt_mode"),
        "fitted_file": bool(seg.get("fitted_file")),
        "file": bool(seg.get("file")),
        "container_status": seg.get("container_status"),
        "slot_vs_audio_delta_ms": (tts_ms - slot_ms) if tts_ms and slot_ms else None,
        "scheduler_matches_timing_map": (
            timing_map_start is None
            or timing_map_end is None
            or (start_ms == timing_map_start and end_ms == timing_map_end)
        ),
        "bleed_risk": bool(tts_ms and slot_ms and tts_ms > slot_ms + 40),
    }
    return snap


def dump_pre_merge_timing_audit(
    segments: list[dict[str, Any]],
    *,
    task_id: str = "",
    timing_map: list | None = None,
    source: str = "pre_merge",
) -> dict[str, Any]:
    """
    TZ Stage 8 — print/dump duration chain immediately before Merge/Render.
    Does not mutate segments.
    """
    rows: list[dict[str, Any]] = []
    for i, seg in enumerate(segments or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        tm_s = tm_e = None
        if timing_map and i < len(timing_map):
            try:
                from engines.timing_fit import _parse_timing

                tm_s, tm_e = _parse_timing(timing_map[i])
            except Exception:
                try:
                    t = timing_map[i]
                    if isinstance(t, dict):
                        tm_s, tm_e = int(t.get("start") or 0), int(t.get("end") or 0)
                except Exception:
                    pass
        snap = snapshot_segment_timing(
            seg,
            index=i,
            stage=source,
            timing_map_start=tm_s,
            timing_map_end=tm_e,
        )
        rows.append(snap)
        # Human block (TZ Stage 8)
        block = (
            "================================================\n"
            f"SEGMENT {i + 1}\n"
            f"UUID {snap['segment_uuid']}\n"
            f"original_duration_ms {snap['original_duration_ms']}\n"
            f"predicted_duration_ms {snap['predicted_duration_ms']}\n"
            f"tts_duration_ms {snap['tts_duration_ms']}\n"
            f"adapted_duration_ms {snap['adapted_duration_ms']}\n"
            f"scheduler_duration_ms {snap['scheduler_duration_ms']}\n"
            f"render_duration_ms {snap['scheduler_duration_ms']}\n"
            f"overflow_ms {snap['overflow_ms']}\n"
            f"underflow_ms {snap['underflow_ms']}\n"
            f"adaptation_status {snap['adaptation_status']}\n"
            f"bleed_risk {snap['bleed_risk']} delta={snap['slot_vs_audio_delta_ms']}\n"
            f"scheduler_matches_timing_map {snap['scheduler_matches_timing_map']}\n"
            "================================================"
        )
        logger.info("TIMING_AUDIT_PRE_MERGE task=%s\n%s", task_id, block)
        _append_debug(
            "H2",
            "timing_lifecycle_audit.py:dump_pre_merge",
            "segment_pre_merge",
            snap,
        )

    bleeds = [r for r in rows if r.get("bleed_risk")]
    executed_with_overflow = [
        r
        for r in rows
        if r.get("adaptation_executed") and _i(r.get("overflow_ms")) > 0
    ]
    summary = {
        "task_id": task_id,
        "source": source,
        "segment_count": len(rows),
        "bleed_risk_count": len(bleeds),
        "adaptation_executed_with_overflow": len(executed_with_overflow),
        "scheduler_equals_timing_map_count": sum(
            1 for r in rows if r.get("scheduler_matches_timing_map")
        ),
    }
    report = {"summary": summary, "segments": rows}
    try:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = _OUT_DIR / f"pre_merge_{task_id or 'unknown'}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["report_path"] = str(path)
    except Exception:
        pass
    _append_debug(
        "H1",
        "timing_lifecycle_audit.py:dump_pre_merge",
        "pre_merge_summary",
        summary,
    )
    logger.warning(
        "TIMING_AUDIT_PRE_MERGE summary task=%s bleeds=%s executed_with_overflow=%s",
        task_id,
        summary["bleed_risk_count"],
        summary["adaptation_executed_with_overflow"],
    )
    return report
