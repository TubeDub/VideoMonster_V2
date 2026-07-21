"""Observability — Dub Engine Stabilization TZ v2.0 P8.

Pipeline metrics, segment history, execution graph stubs.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SegmentHistoryEvent:
    ts: float
    stage: str
    event: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineMetrics:
    stages: dict[str, float] = field(default_factory=dict)
    overlap_count: int = 0
    overflow_count: int = 0
    scheduler_iterations: int = 0
    recovery_count: int = 0
    runtime_checks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record_segment_event(
    seg: dict[str, Any],
    *,
    stage: str,
    event: str,
    detail: str = "",
) -> None:
    hist = list(seg.get("segment_history") or [])
    hist.append(
        SegmentHistoryEvent(
            ts=time.time(), stage=stage, event=event, detail=detail
        ).to_dict()
    )
    # Cap history length
    seg["segment_history"] = hist[-50:]


def build_execution_graph(info: dict[str, Any]) -> dict[str, Any]:
    state = str(info.get("pipeline_state") or "NEW")
    nodes = [
        "Whisper",
        "Translation",
        "Validation",
        "LOCK",
        "Scheduler",
        "AudioTimingOptimizer",
        "TTS",
        "Alignment",
        "Merge",
        "Handoff",
        "Export",
    ]
    return {
        "pipeline_state": state,
        "nodes": nodes,
        "edges": [
            [nodes[i], nodes[i + 1]] for i in range(len(nodes) - 1)
        ],
        "metrics": (info.get("dub_metrics") or {}),
        "runtime_integrity": info.get("runtime_integrity") or {},
    }


def health_dashboard(info: dict[str, Any]) -> dict[str, Any]:
    segs = info.get("segments_data") or []
    missing_tts = sum(
        1
        for s in segs
        if isinstance(s, dict)
        and not s.get("merged_into")
        and not (s.get("file") or s.get("tts_file_path"))
    )
    return {
        "ok": missing_tts == 0 and bool(info.get("translation_locked") or True),
        "pipeline_state": info.get("pipeline_state"),
        "segments": len(segs),
        "missing_tts": missing_tts,
        "translation_locked": bool(info.get("translation_locked")),
        "execution_graph": build_execution_graph(info),
    }
