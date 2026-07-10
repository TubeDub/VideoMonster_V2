"""Per-task pipeline stage timer — task dict + pipeline_timing_<task_id>.json."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from engines.pipeline_profiler import PipelineProfiler, REPORT_STAGES, STAGE_LABELS

TIMER_STAGES = (
    "extract",
    "whisper",
    "translation",
    "natural",
    "validation",
    "translation_post",
    "tts",
    "slot_fit",
    "timing",
    "mux",
    "export",
)

TIMER_LABELS = {
    "extract": "Extract",
    "whisper": "Whisper",
    "translation": "Marian MT",
    "natural": "Qwen / LLM Adaptation",
    "validation": "Validation",
    "translation_post": "Post-processing",
    "tts": "TTS",
    "slot_fit": "Slot Fit",
    "timing": "Timing",
    "mux": "Mux",
    "export": "Export",
}


class PipelineTimer:
    """Tracks stage durations, mirrors PipelineProfiler, writes per-task JSON."""

    def __init__(
        self,
        task_id: str,
        app_dir: Path,
        *,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.task_id = task_id
        self.app_dir = Path(app_dir)
        self.output_dir = self.app_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.output_dir / f"pipeline_timing_{task_id}.json"
        self._on_update = on_update
        self._started_at = time.perf_counter()
        self._active: dict[str, float] = {}
        self._seconds: dict[str, float] = {k: 0.0 for k in TIMER_STAGES}
        self._meta: dict[str, Any] = {}
        self._profiler = PipelineProfiler(task_id, app_dir)

    @property
    def profiler(self) -> PipelineProfiler:
        return self._profiler

    def start(self, stage: str) -> None:
        if stage in self._seconds:
            self._active[stage] = time.perf_counter()
        if stage in REPORT_STAGES:
            self._profiler.start(stage)
        elif stage == "slot_fit":
            self._active["slot_fit"] = time.perf_counter()
        elif stage == "extract":
            self._profiler.start("ffmpeg")

    def stop(self, stage: str) -> float:
        elapsed = 0.0
        if stage in self._seconds:
            t0 = self._active.pop(stage, None)
            if t0 is not None:
                elapsed = max(0.0, time.perf_counter() - t0)
                self._seconds[stage] += elapsed
        if stage in REPORT_STAGES:
            self._profiler.stop(stage)
        elif stage == "extract":
            self._profiler.stop("ffmpeg")
        self._push_task()
        return elapsed

    def add(self, stage: str, seconds: float) -> None:
        if stage in self._seconds and seconds > 0:
            self._seconds[stage] += seconds
        if stage in REPORT_STAGES:
            self._profiler.add(stage, seconds)
        self._push_task()

    def set_meta(self, **kwargs: Any) -> None:
        self._meta.update(kwargs)
        self._profiler.set_meta(**kwargs)

    @property
    def total_seconds(self) -> float:
        return max(0.0, time.perf_counter() - self._started_at)

    def set_translation_breakdown(self, breakdown: dict[str, Any]) -> None:
        """Store Marian / LLM / post-processing split for summary log."""
        self._meta["translation_breakdown"] = dict(breakdown or {})
        buckets = (breakdown or {}).get("ui_buckets") or {}
        if buckets.get("marian_mt"):
            self._seconds["translation"] = float(buckets["marian_mt"])
        if buckets.get("llm_adaptation"):
            self._seconds["natural"] = float(buckets["llm_adaptation"])
        val = float((breakdown or {}).get("validation_sec") or 0)
        post = float(buckets.get("post_processing") or 0)
        if val:
            self._seconds["validation"] = val
        if post:
            self._seconds["translation_post"] = post
        self._push_task()

    def summary_lines(self) -> list[str]:
        from engines.translation_timing import format_duration_clock

        br = self._meta.get("translation_breakdown") or {}
        buckets = br.get("ui_buckets") or {}
        if buckets:
            lines = []
            whisper_sec = self._seconds.get("whisper", 0.0)
            if whisper_sec <= 0:
                whisper_sec = self._profiler._seconds.get("whisper", 0.0)
            if whisper_sec > 0:
                lines.append(f"{'Whisper':.<22} {format_duration_clock(whisper_sec)}")
            marian = float(buckets.get("marian_mt") or self._seconds.get("translation", 0))
            llm = float(buckets.get("llm_adaptation") or self._seconds.get("natural", 0))
            val = float(br.get("validation_sec") or self._seconds.get("validation", 0))
            post = float(buckets.get("post_processing") or self._seconds.get("translation_post", 0))
            if marian > 0:
                lines.append(f"{'Marian MT':.<22} {format_duration_clock(marian)}")
            if llm > 0:
                lines.append(f"{'Qwen adaptation':.<22} {format_duration_clock(llm)}")
            if val > 0:
                lines.append(f"{'Validation':.<22} {format_duration_clock(val)}")
            if post > 0:
                lines.append(f"{'Post-processing':.<22} {format_duration_clock(post)}")
            tts_sec = self._seconds.get("tts", 0.0)
            if tts_sec <= 0:
                tts_sec = self._profiler._seconds.get("tts", 0.0)
            if tts_sec > 0:
                lines.append(f"{'TTS':.<22} {format_duration_clock(tts_sec)}")
            lines.append("")
            lines.append(f"{'Total':.<22} {format_duration_clock(self.total_seconds)}")
            return lines

        lines = []
        resolved: dict[str, float] = {}
        for key in TIMER_STAGES:
            sec = self._seconds.get(key, 0.0)
            if sec <= 0 and key not in ("slot_fit",):
                mapped = {
                    "extract": "ffmpeg",
                    "natural": "naturalizer",
                }.get(key)
                if mapped:
                    sec = self._profiler._seconds.get(mapped, 0.0)
            resolved[key] = sec
            label = TIMER_LABELS.get(key, key)
            lines.append(f"{label:.<22} {sec:5.2f} s")
        lines.append("")
        lines.append(f"{'Total':.<22} {self.total_seconds:5.2f} s")
        slowest = max(resolved.items(), key=lambda kv: kv[1], default=(None, 0.0))
        if slowest[0] and slowest[1] > 0:
            lines.append(
                f"{'Slowest':.<22} {TIMER_LABELS.get(slowest[0], slowest[0])} "
                f"({slowest[1]:.2f} s)"
            )
        return lines

    def to_dict(self) -> dict[str, Any]:
        stages = {}
        for key in TIMER_STAGES:
            sec = self._seconds.get(key, 0.0)
            if sec <= 0:
                if key == "extract":
                    sec = self._profiler._seconds.get("ffmpeg", 0.0)
                elif key == "natural":
                    sec = self._profiler._seconds.get("naturalizer", 0.0)
            stages[key] = round(sec, 3)
        return {
            "task_id": self.task_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_sec": round(self.total_seconds, 3),
            "stages": stages,
            "meta": dict(self._meta),
        }

    def _push_task(self) -> None:
        if self._on_update:
            try:
                self._on_update(self.to_dict())
            except Exception:
                pass

    def write_json(self) -> str:
        payload = self.to_dict()
        self.json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._push_task()
        return str(self.json_path)

    def finalize(
        self,
        *,
        video_path: str | None = None,
        success: bool = True,
    ) -> tuple[str, str]:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        summary = self.summary_lines()
        block = [
            f"=== pipeline_timing task={self.task_id} ts={ts} success={success} ===",
            *summary,
            "",
        ]
        log_path = self.app_dir / "output" / "dev" / "performance_report.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(block))

        json_path = self.write_json()
        perf_path = self._profiler.finalize(video_path=video_path, success=success)
        return json_path, perf_path
