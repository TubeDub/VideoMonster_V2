"""P619 Voice Metrics + P624 Performance Budget."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class VoiceMetrics:
    synth_count: int = 0
    error_count: int = 0
    retry_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    total_ms: float = 0.0
    by_provider: dict[str, int] = field(default_factory=dict)
    quality_sum: float = 0.0
    quality_n: int = 0

    def to_dict(self) -> dict[str, Any]:
        avg = (self.total_ms / self.synth_count) if self.synth_count else 0.0
        err_pct = (100.0 * self.error_count / self.synth_count) if self.synth_count else 0.0
        retry_pct = (100.0 * self.retry_count / self.synth_count) if self.synth_count else 0.0
        cache_pct = (
            100.0 * self.cache_hits / (self.cache_hits + self.cache_misses)
            if (self.cache_hits + self.cache_misses)
            else 0.0
        )
        q = (self.quality_sum / self.quality_n) if self.quality_n else 0.0
        return {
            "synth_count": self.synth_count,
            "avg_synth_ms": round(avg, 2),
            "error_pct": round(err_pct, 2),
            "retry_pct": round(retry_pct, 2),
            "quality_avg": round(q, 2),
            "cache_hit_pct": round(cache_pct, 2),
            "by_provider": dict(self.by_provider),
            "total_ms": round(self.total_ms, 2),
        }


_LOCK = threading.Lock()
_METRICS = VoiceMetrics()

# P624 budgets (soft)
BUDGET_MAX_SYNTH_MS = 30_000.0
BUDGET_MAX_CACHE_MB = 2048.0
BUDGET_MAX_PARALLEL = 8


def record_synthesis(
    *,
    provider: str,
    elapsed_ms: float,
    ok: bool,
    cached: bool = False,
    retried: bool = False,
    quality: float | None = None,
) -> None:
    with _LOCK:
        _METRICS.synth_count += 1
        _METRICS.total_ms += float(elapsed_ms or 0)
        _METRICS.by_provider[provider] = _METRICS.by_provider.get(provider, 0) + 1
        if not ok:
            _METRICS.error_count += 1
        if cached:
            _METRICS.cache_hits += 1
        else:
            _METRICS.cache_misses += 1
        if retried:
            _METRICS.retry_count += 1
        if quality is not None:
            _METRICS.quality_sum += float(quality)
            _METRICS.quality_n += 1


def get_metrics() -> dict[str, Any]:
    with _LOCK:
        return _METRICS.to_dict()


def reset_metrics() -> None:
    global _METRICS
    with _LOCK:
        _METRICS = VoiceMetrics()


def check_performance_budget(
    *,
    last_synth_ms: float | None = None,
    cache_bytes: int | None = None,
    parallel: int | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    if last_synth_ms is not None and last_synth_ms > BUDGET_MAX_SYNTH_MS:
        issues.append("synth_time_over_budget")
    if cache_bytes is not None and (cache_bytes / (1024 * 1024)) > BUDGET_MAX_CACHE_MB:
        issues.append("cache_size_over_budget")
    if parallel is not None and parallel > BUDGET_MAX_PARALLEL:
        issues.append("parallel_over_budget")
    return {"ok": len(issues) == 0, "issues": issues}
