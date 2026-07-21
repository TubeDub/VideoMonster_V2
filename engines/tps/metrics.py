"""TPS Performance Dashboard metrics (TPS5)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TPSMetrics:
    task_id: str = ""
    fast_path_count: int = 0
    retry_path_count: int = 0
    llm_judge_count: int = 0
    manual_review_count: int = 0
    avg_segment_ms: float = 0.0
    p95_segment_ms: float = 0.0
    avg_llm_calls_per_segment: float = 0.0
    max_llm_calls_per_segment: int = 0
    reject_reason_histogram: dict[str, int] = field(default_factory=dict)
    approved_text_mutation_attempts: int = 0
    dual_writer_violations: int = 0
    segment_count: int = 0
    segment_ms: list[float] = field(default_factory=list)
    llm_calls: list[int] = field(default_factory=list)

    def add_reason(self, code: str) -> None:
        key = str(code or "unknown")
        self.reject_reason_histogram[key] = int(
            self.reject_reason_histogram.get(key) or 0
        ) + 1

    def finalize(self) -> None:
        self.segment_count = max(
            self.segment_count,
            self.fast_path_count
            + self.retry_path_count
            + self.llm_judge_count
            + self.manual_review_count,
        )
        if self.segment_ms:
            self.avg_segment_ms = round(
                sum(self.segment_ms) / len(self.segment_ms), 2
            )
            ordered = sorted(self.segment_ms)
            idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
            self.p95_segment_ms = round(ordered[idx], 2)
        if self.llm_calls:
            self.avg_llm_calls_per_segment = round(
                sum(self.llm_calls) / len(self.llm_calls), 3
            )
            self.max_llm_calls_per_segment = max(self.llm_calls)

    def to_dict(self) -> dict[str, Any]:
        self.finalize()
        d = asdict(self)
        d.pop("segment_ms", None)
        d.pop("llm_calls", None)
        d["generated_at"] = int(time.time())
        return d


def write_tps_metrics(
    app_dir: Path | str,
    metrics: TPSMetrics,
    *,
    session_dir: Path | str | None = None,
) -> Path:
    metrics.finalize()
    payload = metrics.to_dict()
    # Prefer session dir
    if session_dir:
        out = Path(session_dir) / "tps_metrics.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    root = Path(app_dir) / "output" / "sessions" / str(metrics.task_id or "unknown")
    root.mkdir(parents=True, exist_ok=True)
    out = root / "tps_metrics.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Also mirror under quality/analytics
    try:
        q = Path(app_dir) / "quality" / "analytics"
        q.mkdir(parents=True, exist_ok=True)
        (q / f"tps_metrics_{metrics.task_id}.json").write_text(
            out.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except Exception:
        pass
    return out
