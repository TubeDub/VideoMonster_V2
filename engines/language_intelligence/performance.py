"""Performance guard — fast mode when budget exceeded."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from engines.language_intelligence.config import fast_mode_budget_ms


@dataclass
class PerformanceGuard:
    budget_ms: float = field(default_factory=fast_mode_budget_ms)
    fast_mode: bool = False
    segment_times: list[float] = field(default_factory=list)

    def start_segment(self) -> float:
        return time.perf_counter()

    def end_segment(self, t0: float) -> None:
        ms = (time.perf_counter() - t0) * 1000.0
        self.segment_times.append(ms)
        if ms > self.budget_ms * 1.5:
            self.fast_mode = True

    @property
    def avg_ms(self) -> float:
        if not self.segment_times:
            return 0.0
        return sum(self.segment_times) / len(self.segment_times)
