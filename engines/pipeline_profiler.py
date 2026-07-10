"""Per-task pipeline performance profiler — append-only report log."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.hardware_probe import format_hardware_summary, probe_hardware

REPORT_STAGES = (
    "whisper",
    "segmentation",
    "translation",
    "naturalizer",
    "tts",
    "timing",
    "ffmpeg",
    "mux",
)

STAGE_LABELS = {
    "whisper": "Whisper",
    "segmentation": "Segmentation",
    "translation": "Translation",
    "naturalizer": "Naturalizer",
    "tts": "TTS",
    "timing": "Timing",
    "ffmpeg": "FFmpeg",
    "mux": "Mux",
}


class PipelineProfiler:
    """Measures pipeline stage durations and appends to output/dev/performance_report.log."""

    def __init__(self, task_id: str, app_dir: Path):
        self.task_id = task_id
        self.log_dir = app_dir / "output" / "dev"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = self.log_dir / "performance_report.log"
        self._started_at = time.perf_counter()
        self._active: dict[str, float] = {}
        self._seconds: dict[str, float] = {k: 0.0 for k in REPORT_STAGES}
        self._meta: dict[str, Any] = {}

    def start(self, stage: str) -> None:
        if stage not in self._seconds:
            return
        self._active[stage] = time.perf_counter()

    def stop(self, stage: str) -> float:
        if stage not in self._seconds:
            return 0.0
        t0 = self._active.pop(stage, None)
        if t0 is None:
            return self._seconds.get(stage, 0.0)
        elapsed = max(0.0, time.perf_counter() - t0)
        self._seconds[stage] = self._seconds.get(stage, 0.0) + elapsed
        return elapsed

    def add(self, stage: str, seconds: float) -> None:
        if stage in self._seconds and seconds > 0:
            self._seconds[stage] += seconds

    def set_meta(self, **kwargs: Any) -> None:
        self._meta.update(kwargs)

    @property
    def total_seconds(self) -> float:
        return max(0.0, time.perf_counter() - self._started_at)

    def summary_lines(self) -> list[str]:
        lines = []
        for key in REPORT_STAGES:
            label = STAGE_LABELS[key]
            sec = self._seconds.get(key, 0.0)
            lines.append(f"{label:.<15} {sec:5.2f} сек")
        lines.append("")
        lines.append(f"{'Итого':.<15} {self.total_seconds:5.2f} сек")
        return lines

    def finalize(self, *, video_path: str | None = None, success: bool = True) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        hw = self._meta.get("hardware") or probe_hardware()
        block = [
            f"=== task={self.task_id} ts={ts} success={success} ===",
        ]
        if video_path:
            block.append(f"video={video_path}")
        for k, v in self._meta.items():
            if k == "hardware":
                continue
            block.append(f"{k}={v}")
        block.extend(format_hardware_summary(hw))
        block.append("")
        block.extend(self.summary_lines())
        block.append("")

        with self.report_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(block))

        return str(self.report_path)
