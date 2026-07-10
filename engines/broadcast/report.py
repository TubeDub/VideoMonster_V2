"""BroadcastReport — TV-grade quality reporting."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from engines.broadcast.config import REPORT_FILE


def build_broadcast_report(
    *,
    task_id: str,
    segments: list[dict[str, Any]],
    app_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Overall Quality Score 0-100, broken segments, per-engine corruption stats.
    """
    total = len(segments)
    broken: list[dict[str, Any]] = []
    engine_corruption: dict[str, int] = {}
    fuzzy_count = 0
    failed_count = 0
    scores: list[float] = []

    for seg in segments:
        idx = seg.get("segment_index", seg.get("index", -1))
        score = float(seg.get("quality_score") or seg.get("broadcast_score") or 0)
        scores.append(score)
        incidents = seg.get("restore_incidents") or seg.get("incidents") or []
        gate_fatals = seg.get("gate_fatals") or []

        for inc in incidents:
            if inc.get("failed"):
                failed_count += 1
                broken.append(
                    {
                        "segment_index": idx,
                        "reason": inc.get("error", "unrecoverable token"),
                        "stage": "smart_restore",
                    }
                )
            elif inc.get("term_id"):
                fuzzy_count += 1
                eng = inc.get("engine") or "unknown"
                engine_corruption[eng] = engine_corruption.get(eng, 0) + 1

        for fatal in gate_fatals:
            if fatal.get("fatal"):
                eng = fatal.get("engine") or "unknown"
                engine_corruption[eng] = engine_corruption.get(eng, 0) + 1
                broken.append(
                    {
                        "segment_index": idx,
                        "reason": fatal.get("error", "validation_gate failed"),
                        "stage": "validation_gate",
                        "engine": eng,
                    }
                )

        if seg.get("failed"):
            failed_count += 1
            broken.append(
                {
                    "segment_index": idx,
                    "reason": seg.get("fail_reason", "segment blocked"),
                    "stage": seg.get("fail_stage", "broadcast"),
                }
            )

    avg_score = sum(scores) / max(len(scores), 1) if scores else 0.0
    # Penalize fuzzy restores and failures for broadcast grade
    penalty = min(40, fuzzy_count * 2 + failed_count * 10)
    overall = max(0.0, min(100.0, avg_score - penalty))

    report = {
        "version": 1,
        "task_id": task_id,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overall_quality_score": round(overall, 1),
        "segment_count": total,
        "broken_segment_count": len(broken),
        "fuzzy_restore_count": fuzzy_count,
        "failed_segment_count": failed_count,
        "broken_segments": broken,
        "engine_corruption_stats": engine_corruption,
        "segments": segments,
    }

    if app_dir:
        path = app_dir / "data" / REPORT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def export_broadcast_report_text(report: dict[str, Any]) -> str:
    lines = [
        "TubeDub Broadcast Quality Report",
        f"Task: {report.get('task_id', '')}",
        f"Overall Quality Score: {report.get('overall_quality_score', 0)}/100",
        f"Segments: {report.get('segment_count', 0)}",
        f"Broken: {report.get('broken_segment_count', 0)}",
        f"Fuzzy restores: {report.get('fuzzy_restore_count', 0)}",
        f"Failed: {report.get('failed_segment_count', 0)}",
        "",
        "Engine corruption stats:",
    ]
    for eng, n in (report.get("engine_corruption_stats") or {}).items():
        lines.append(f"  {eng}: {n} incident(s)")
    lines.append("")
    lines.append("Broken segments:")
    for b in report.get("broken_segments") or []:
        lines.append(
            f"  #{b.get('segment_index')} [{b.get('stage')}]: {b.get('reason', '')}"
        )
    return "\n".join(lines)
