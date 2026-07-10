"""Structured performance artifacts for TubeDub pipeline runs (TZ: diagnostics first).

Writes per-task:
  output/diagnostics/<task_id>/performance_report.json
  output/diagnostics/<task_id>/timeline.json

Golden rule: collect data before changing architecture/models/prompts/algorithms.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.pipeline_performance")

SCHEMA_REPORT = "tubedub.performance_report.v1"
SCHEMA_TIMELINE = "tubedub.timeline.v1"
BOTTLENECK_THRESHOLD_PCT = 50.0


def perf_debug_enabled() -> bool:
    """Rich debug fields in artifacts — hidden from end-user UI."""
    return os.getenv("VM_PERF_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def perf_profiling_enabled() -> bool:
    """Enable cProfile capture (benchmark script / VM_PERF_PROFILE=1)."""
    return os.getenv("VM_PERF_PROFILE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def diagnostics_dir(app_dir: Path, task_id: str) -> Path:
    return Path(app_dir) / "output" / "diagnostics" / str(task_id)


def _resource_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from engines.hardware_probe import probe_hardware

        hw = probe_hardware()
        out["hardware"] = {
            "platform": hw.get("platform"),
            "cuda_available": bool(hw.get("cuda_available")),
            "cuda_devices": hw.get("cuda_devices", 0),
        }
    except Exception:
        out["hardware"] = {}
    try:
        import psutil  # type: ignore[import-untyped]

        vm = psutil.virtual_memory()
        out["cpu_percent"] = psutil.cpu_percent(interval=None)
        out["ram_used_mb"] = round(vm.used / (1024 * 1024), 1)
        out["ram_total_mb"] = round(vm.total / (1024 * 1024), 1)
        out["ram_percent"] = vm.percent
    except Exception:
        pass
    return out


def _llm_stats() -> dict[str, Any]:
    out: dict[str, Any] = {
        "calls": 0,
        "total_ms": 0.0,
        "avg_ms": 0.0,
        "skip_reasons": {},
        "errors": [],
        "retries": 0,
    }
    try:
        from engines.translation_adapt import get_llm_calls, get_llm_status

        calls = list(get_llm_calls() or [])
        out["calls"] = len(calls)
        ms_vals = [float(c.get("ms") or 0) for c in calls if c.get("ms")]
        out["total_ms"] = round(sum(ms_vals), 1)
        out["avg_ms"] = round(out["total_ms"] / len(ms_vals), 1) if ms_vals else 0.0
        out["errors"] = [
            str(c.get("error") or c.get("failure") or "")
            for c in calls
            if c.get("error") or c.get("failure")
        ][:50]
        out["retries"] = sum(int(c.get("attempt") or 1) - 1 for c in calls if c.get("attempt"))

        skip: dict[str, int] = {}
        for row in get_llm_status() or []:
            reason = str(row.get("skip_reason") or "").strip()
            if reason:
                skip[reason] = skip.get(reason, 0) + 1
        out["skip_reasons"] = skip
    except Exception as exc:
        out["collect_error"] = str(exc)
    return out


def _find_bottleneck(stages: dict[str, float], total_sec: float) -> dict[str, Any] | None:
    if not stages or total_sec <= 0:
        return None
    best_key = max(stages.items(), key=lambda kv: kv[1], default=(None, 0.0))
    stage, sec = best_key
    if not stage or sec <= 0:
        return None
    pct = round(100.0 * sec / total_sec, 2)
    return {
        "stage": stage,
        "duration_sec": round(sec, 3),
        "percent_of_total": pct,
        "exceeds_threshold": pct >= BOTTLENECK_THRESHOLD_PCT,
        "threshold_percent": BOTTLENECK_THRESHOLD_PCT,
    }


def build_performance_report(
    task_id: str,
    *,
    app_dir: Path,
    task_info: dict[str, Any] | None = None,
    pipeline_timer_dict: dict[str, Any] | None = None,
    success: bool = True,
    video_path: str | None = None,
) -> dict[str, Any]:
    """Assemble performance_report.json payload from existing collectors."""
    info = dict(task_info or {})
    timer = dict(pipeline_timer_dict or info.get("pipeline_timing") or {})
    stages = dict(timer.get("stages") or {})
    total_sec = float(timer.get("total_sec") or 0)
    if total_sec <= 0:
        total_sec = sum(float(v) for v in stages.values())

    stage_pct = {
        k: round(100.0 * float(v) / total_sec, 2)
        for k, v in stages.items()
        if total_sec > 0 and float(v) > 0
    }

    perf = dict(info.get("pipeline_performance") or {})
    llm_diag = dict(info.get("llm_diagnostics") or {})

    report: dict[str, Any] = {
        "schema": SCHEMA_REPORT,
        "golden_rule": (
            "No architecture/model/prompt/algorithm changes until bottleneck is "
            "identified from profiling data (this report + timeline.json)."
        ),
        "task_id": task_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "success": bool(success),
        "video_path": video_path or info.get("filename") or "",
        "total_sec": round(total_sec, 3),
        "stages_sec": {k: round(float(v), 3) for k, v in stages.items()},
        "stages_percent": stage_pct,
        "bottleneck": _find_bottleneck(
            {k: float(v) for k, v in stages.items()}, total_sec
        ),
        "translation_breakdown": (timer.get("meta") or {}).get("translation_breakdown"),
        "pipeline_conveyor_timing": info.get("pipeline_conveyor_timing"),
        "segment_summary": {
            "total": len(info.get("segments_data") or info.get("source_segments") or []),
            "slow_segments": len(perf.get("slow_segments") or []),
            "slowest_segment": perf.get("slowest_segment"),
        },
        "llm": _llm_stats(),
        "llm_diagnostics": llm_diag if llm_diag else None,
        "resource_usage": _resource_snapshot(),
        "status": info.get("status") or ("done" if success else "error"),
        "error_code": info.get("error_code"),
    }

    if perf_debug_enabled():
        report["debug"] = {
            "pipeline_performance": perf,
            "runtime_diagnostics": info.get("runtime_diagnostics"),
            "adaptation_capabilities": info.get("adaptation_capabilities"),
            "llm_effectiveness": info.get("llm_effectiveness"),
            "env": {
                "VM_PERF_DEBUG": os.getenv("VM_PERF_DEBUG"),
                "VM_PERF_PROFILE": os.getenv("VM_PERF_PROFILE"),
                "VM_PIPELINE_CONVEYOR": os.getenv("VM_PIPELINE_CONVEYOR"),
                "VM_DEBUG_MODE": os.getenv("VM_DEBUG_MODE"),
            },
        }

    return report


def build_timeline(
    task_id: str,
    *,
    app_dir: Path,
    task_info: dict[str, Any] | None = None,
    pipeline_timer_dict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge stage/segment/agent events into a single timeline.json."""
    info = dict(task_info or {})
    events: list[dict[str, Any]] = []

    def _add(
        *,
        ts_epoch: float | None,
        stage: str,
        event: str,
        source: str,
        **extra: Any,
    ) -> None:
        if ts_epoch is None:
            return
        events.append(
            {
                "ts_epoch": round(float(ts_epoch), 3),
                "ts": datetime.fromtimestamp(float(ts_epoch), tz=timezone.utc).isoformat(),
                "stage": stage,
                "event": event,
                "source": source,
                **extra,
            }
        )

    # Progress tracker stage_times (from record_stage_start/end)
    try:
        from engines.pipeline_progress_tracker import _get_state

        st = _get_state(task_id) or {}
        for stage, row in (st.get("stage_times") or {}).items():
            if not isinstance(row, dict):
                continue
            started = row.get("started_at")
            ended = row.get("ended_at")
            if started:
                _add(ts_epoch=float(started), stage=stage, event="start", source="progress_tracker")
            if ended:
                dur = row.get("duration_sec")
                _add(
                    ts_epoch=float(ended),
                    stage=stage,
                    event="end",
                    source="progress_tracker",
                    duration_sec=dur,
                )
    except Exception:
        pass

    # Pipeline timer stages (wall-clock buckets — no per-stage start, use ordering)
    timer = dict(pipeline_timer_dict or info.get("pipeline_timing") or {})
    stages_map = timer.get("stages") or {}
    if not isinstance(stages_map, dict):
        stages_map = {}
    order = list(stages_map.keys())
    base = time.time() - float(timer.get("total_sec") or 0)
    cursor = base
    for stage in order:
        sec = float(stages_map.get(stage) or 0)
        if sec <= 0:
            continue
        _add(ts_epoch=cursor, stage=stage, event="stage_block_start", source="pipeline_timer")
        cursor += sec
        _add(
            ts_epoch=cursor,
            stage=stage,
            event="stage_block_end",
            source="pipeline_timer",
            duration_sec=round(sec, 3),
        )

    # Developer agent timeline
    for row in info.get("developer_timeline") or []:
        if not isinstance(row, dict):
            continue
        ts = row.get("ts") or row.get("timestamp")
        if ts is None:
            continue
        try:
            ts_f = float(ts)
        except (TypeError, ValueError):
            continue
        _add(
            ts_epoch=ts_f,
            stage=str(row.get("agent") or row.get("stage") or "agent"),
            event=str(row.get("status") or row.get("event") or "event"),
            source="developer_timeline",
            detail=row.get("detail"),
        )

    # Runtime diagnostics recorder
    for row in info.get("runtime_diagnostics") or []:
        if not isinstance(row, dict):
            continue
        stage = str(row.get("stage") or row.get("name") or "runtime")
        dur_ms = row.get("duration_ms")
        _add(
            ts_epoch=time.time(),
            stage=stage,
            event=str(row.get("status") or "complete"),
            source="runtime_diagnostics",
            duration_ms=dur_ms,
            memory_mb=row.get("memory_mb"),
        )

    events.sort(key=lambda e: e.get("ts_epoch") or 0)

    gaps: list[dict[str, Any]] = []
    for i in range(1, len(events)):
        prev = events[i - 1]
        cur = events[i]
        gap = float(cur["ts_epoch"]) - float(prev["ts_epoch"])
        if gap >= 5.0 and prev.get("event", "").endswith("end") and cur.get("event", "").startswith("stage"):
            gaps.append(
                {
                    "after_stage": prev.get("stage"),
                    "before_stage": cur.get("stage"),
                    "idle_sec": round(gap, 2),
                }
            )

    return {
        "schema": SCHEMA_TIMELINE,
        "task_id": task_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "idle_gaps_sec": gaps[:20],
        "events": events,
    }


def write_performance_artifacts(
    task_id: str,
    *,
    app_dir: Path,
    task_info: dict[str, Any] | None = None,
    pipeline_timer_dict: dict[str, Any] | None = None,
    success: bool = True,
    video_path: str | None = None,
) -> dict[str, str]:
    """Write performance_report.json + timeline.json; return paths."""
    out_dir = diagnostics_dir(app_dir, task_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = build_performance_report(
        task_id,
        app_dir=app_dir,
        task_info=task_info,
        pipeline_timer_dict=pipeline_timer_dict,
        success=success,
        video_path=video_path,
    )
    timeline = build_timeline(
        task_id,
        app_dir=app_dir,
        task_info=task_info,
        pipeline_timer_dict=pipeline_timer_dict,
    )

    report_path = out_dir / "performance_report.json"
    timeline_path = out_dir / "timeline.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    timeline_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    bottleneck = report.get("bottleneck") or {}
    if bottleneck.get("exceeds_threshold"):
        logger.warning(
            "[Perf] task=%s bottleneck: %s = %.1f%% (threshold %.0f%%)",
            task_id,
            bottleneck.get("stage"),
            bottleneck.get("percent_of_total") or 0,
            BOTTLENECK_THRESHOLD_PCT,
        )
    else:
        logger.info(
            "[Perf] task=%s artifacts written total=%.1fs slowest=%s",
            task_id,
            report.get("total_sec") or 0,
            (bottleneck or {}).get("stage"),
        )

    return {
        "performance_report_json": str(report_path),
        "timeline_json": str(timeline_path),
    }
