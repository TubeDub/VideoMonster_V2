"""Benchmark artifacts — performance_report, timeline, quality_report."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.streamdub.types import QualityGrade, StreamDubMode, StreamSegment


def artifacts_dir(app_dir: Path, project_id: str) -> Path:
    return app_dir / "output" / "streamdub" / project_id / "diagnostics"


def write_quality_report(
    app_dir: Path,
    project_id: str,
    segments: list[StreamSegment],
    *,
    mode: StreamDubMode,
    stats: dict[str, Any],
) -> str:
    counts = {g.value: 0 for g in QualityGrade}
    routes: dict[str, int] = {}
    for seg in segments:
        if seg.quality:
            counts[seg.quality.value] += 1
        routes[seg.route] = routes.get(seg.route, 0) + 1

    total = max(1, len(segments))
    payload = {
        "schema": "tubedub.streamdub.quality_report.v1",
        "project_id": project_id,
        "mode": mode.value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "segments_total": len(segments),
        "quality_counts": counts,
        "fast_only_pct": round(100.0 * counts.get("GOOD", 0) / total, 1),
        "llm_pct": round(
            100.0
            * (counts.get("MEDIUM", 0) + counts.get("BAD", 0))
            / total,
            1,
        ),
        "routes": routes,
        "target_llm_pct": "5-10",
        "target_fast_pct": "90-95",
        "meets_target": counts.get("GOOD", 0) / total >= 0.90,
        "stats": stats,
        "segments": [
            {
                "index": s.index,
                "quality": s.quality.value if s.quality else None,
                "quality_score": s.quality_score,
                "route": s.route,
                "llm_refined": s.llm_refined,
                "issues": s.quality_issues[:5],
            }
            for s in segments
        ],
    }
    out_dir = artifacts_dir(app_dir, project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "quality_report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def write_timeline(
    app_dir: Path,
    project_id: str,
    events: list[dict[str, Any]],
) -> str:
    payload = {
        "schema": "tubedub.streamdub.timeline.v1",
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "events": events,
    }
    out_dir = artifacts_dir(app_dir, project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "timeline.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def write_performance_report(
    app_dir: Path,
    project_id: str,
    *,
    mode: StreamDubMode,
    stage_timings: dict[str, float],
    stats: dict[str, Any],
    success: bool,
) -> str:
    total = sum(stage_timings.values()) or 0.001
    pct = {k: round(100.0 * v / total, 2) for k, v in stage_timings.items()}
    bottleneck = max(stage_timings.items(), key=lambda kv: kv[1], default=(None, 0))

    payload = {
        "schema": "tubedub.streamdub.performance_report.v1",
        "project_id": project_id,
        "mode": mode.value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "total_sec": round(total, 3),
        "stages_sec": {k: round(v, 3) for k, v in stage_timings.items()},
        "stages_percent": pct,
        "bottleneck": {
            "stage": bottleneck[0],
            "duration_sec": round(bottleneck[1], 3),
            "percent_of_total": pct.get(bottleneck[0], 0) if bottleneck[0] else 0,
        },
        "stats": stats,
    }
    out_dir = artifacts_dir(app_dir, project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "performance_report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


class TimelineRecorder:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._t0 = time.time()

    def start(self, stage: str, **meta: Any) -> None:
        self._events.append(
            {
                "ts_epoch": round(time.time(), 3),
                "offset_sec": round(time.time() - self._t0, 3),
                "stage": stage,
                "event": "start",
                "meta": meta,
            }
        )

    def end(self, stage: str, duration_sec: float, **meta: Any) -> None:
        self._events.append(
            {
                "ts_epoch": round(time.time(), 3),
                "offset_sec": round(time.time() - self._t0, 3),
                "stage": stage,
                "event": "end",
                "duration_sec": round(duration_sec, 3),
                "meta": meta,
            }
        )

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)
