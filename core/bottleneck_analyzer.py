"""Bottleneck Analyzer — automatic pipeline bottleneck detection (TZ #8 §10).

Determines which pipeline stage consumes the most time and produces actionable
recommendations for the Performance Optimizer. Read-only with respect to all
restricted modules — only calls public optimizer APIs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.bottleneck_analyzer")

PIPELINE_STAGES = (
    "whisper", "cleaner", "translator", "review",
    "timing", "voice", "mix", "export",
)

_STAGE_LABELS = {
    "whisper": "Whisper",
    "cleaner": "Cleaner",
    "translator": "Translator",
    "translation": "Translator",
    "review": "AI Review",
    "ai_adaptation": "AI Review",
    "timing": "Timing",
    "voice": "Voice",
    "tts": "Voice",
    "mix": "Mix",
    "export": "Export",
}


@dataclass
class StageBottleneck:
    stage: str
    label: str
    duration_s: float
    percent: float
    avg_wait_s: float = 0.0
    error_count: int = 0
    throughput: float = 0.0


@dataclass
class BottleneckReport:
    stages: list[StageBottleneck] = field(default_factory=list)
    primary: str = ""
    primary_percent: float = 0.0
    recommendations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [
                {
                    "stage": s.stage,
                    "label": s.label,
                    "duration_s": round(s.duration_s, 2),
                    "percent": round(s.percent, 1),
                    "avg_wait_s": round(s.avg_wait_s, 2),
                    "error_count": s.error_count,
                    "throughput": round(s.throughput, 3),
                }
                for s in self.stages
            ],
            "primary": self.primary,
            "primary_percent": round(self.primary_percent, 1),
            "recommendations": self.recommendations,
        }


class BottleneckAnalyzer:
    """Analyse stage durations and produce optimizer recommendations (§10)."""

    def analyze(
        self,
        stage_metrics: dict[str, dict[str, Any]],
        *,
        queue_stats: dict[str, dict[str, Any]] | None = None,
    ) -> BottleneckReport:
        """Compute bottleneck percentages from per-stage metrics."""
        report = BottleneckReport()
        if not stage_metrics:
            return report

        durations: dict[str, float] = {}
        for stage, m in stage_metrics.items():
            busy_ms = float(m.get("busy_ms") or m.get("duration_ms") or 0)
            wait_ms = float(m.get("wait_ms") or 0)
            processed = int(m.get("processed") or m.get("chunks_done") or 0)
            errors = int(m.get("errors") or 0)
            key = self._normalize_stage(stage)
            durations[key] = durations.get(key, 0.0) + busy_ms / 1000.0
            q = (queue_stats or {}).get(stage, {})
            avg_wait = float(q.get("avg_wait_s") or wait_ms / 1000.0)
            throughput = processed / max(0.001, busy_ms / 1000.0) if busy_ms else 0.0
            report.stages.append(StageBottleneck(
                stage=key,
                label=_STAGE_LABELS.get(key, key.title()),
                duration_s=durations[key],
                percent=0.0,
                avg_wait_s=avg_wait,
                error_count=errors,
                throughput=throughput,
            ))

        total = sum(s.duration_s for s in report.stages) or 1.0
        for s in report.stages:
            s.percent = (s.duration_s / total) * 100.0

        report.stages.sort(key=lambda x: x.percent, reverse=True)
        if report.stages:
            report.primary = report.stages[0].stage
            report.primary_percent = report.stages[0].percent

        report.recommendations = self._build_recommendations(report)
        return report

    def _build_recommendations(self, report: BottleneckReport) -> list[dict[str, Any]]:
        recs: list[dict[str, Any]] = []
        if not report.stages:
            return recs

        primary = report.stages[0]
        recs.append({
            "severity": "high",
            "category": "bottleneck",
            "cause": primary.label,
            "detail": f"Stage consumes {primary.percent:.0f}% of pipeline time",
            "action": self._action_for(primary),
        })

        for stage in report.stages[1:3]:
            if stage.avg_wait_s >= 5.0:
                recs.append({
                    "severity": "medium",
                    "category": "queue_wait",
                    "cause": stage.label,
                    "detail": f"Average wait {stage.avg_wait_s:.1f}s",
                    "action": f"Increase queue capacity for {stage.label}",
                })

        llm_stages = {"translator", "review", "ai_adaptation"}
        for stage in report.stages:
            if stage.stage in llm_stages and stage.percent >= 30.0:
                recs.append({
                    "severity": "high",
                    "category": "llm",
                    "cause": "LLM",
                    "detail": f"High response time on {stage.label}",
                    "action": "Consider a faster model or increase LLM concurrency",
                })
        return recs

    @staticmethod
    def _action_for(stage: StageBottleneck) -> str:
        mapping = {
            "whisper": "Enable GPU for Whisper or reduce chunk size",
            "translator": "Increase Translation queue or add LLM workers",
            "review": "Increase AI Review queue or use faster review model",
            "voice": "Increase TTS parallelism or enable GPU TTS",
            "mix": "Enable GPU mixing or reduce concurrent mix jobs",
        }
        return mapping.get(stage.stage, f"Increase resources for {stage.label}")

    def apply_to_optimizer(self, report: BottleneckReport) -> None:
        """Forward recommendations to Performance Optimizer (§10, §17)."""
        if not report.primary:
            return
        try:
            from core.performance_optimizer import get_performance_optimizer, optimizer_enabled

            if not optimizer_enabled():
                return
            opt = get_performance_optimizer()
            plan = opt.plan()
            opt.rebalance_for_bottleneck(plan, report.primary)
            logger.info(
                "[BOTTLENECK] rebalanced for %s (%.0f%%)",
                report.primary, report.primary_percent,
            )
        except Exception as exc:
            logger.debug("[BOTTLENECK] optimizer apply failed: %s", exc)

    @staticmethod
    def _normalize_stage(stage: str) -> str:
        aliases = {
            "translation": "translator",
            "ai_adaptation": "review",
            "tts": "voice",
        }
        s = stage.lower().strip()
        return aliases.get(s, s)


_analyzer: BottleneckAnalyzer | None = None


def get_bottleneck_analyzer() -> BottleneckAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = BottleneckAnalyzer()
    return _analyzer
