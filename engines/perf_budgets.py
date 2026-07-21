"""Performance budgets — Dub Engine Stabilization TZ v2.0 P7."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


SCHEDULER_BUDGET_MS = 20.0
MERGE_BUDGET_MS = 30.0
ALIGNMENT_BUDGET_MS = 50.0
RUNTIME_INTEGRITY_BUDGET_MS = 10.0
DIAGNOSTICS_BUDGET_MS = 5.0

BUDGETS: dict[str, float] = {
    "scheduler": SCHEDULER_BUDGET_MS,
    "merge": MERGE_BUDGET_MS,
    "alignment": ALIGNMENT_BUDGET_MS,
    "runtime_integrity": RUNTIME_INTEGRITY_BUDGET_MS,
    "diagnostics": DIAGNOSTICS_BUDGET_MS,
}


class PerformanceBudgetError(RuntimeError):
    code = "performance_budget"


@dataclass
class BudgetSample:
    name: str
    elapsed_ms: float
    budget_ms: float

    @property
    def ok(self) -> bool:
        return self.elapsed_ms <= self.budget_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "budget_ms": self.budget_ms,
            "ok": self.ok,
        }


@contextmanager
def measure_budget(name: str, *, enforce: bool = False) -> Iterator[BudgetSample]:
    budget = float(BUDGETS.get(name, 0.0))
    t0 = time.perf_counter()
    sample = BudgetSample(name=name, elapsed_ms=0.0, budget_ms=budget)
    try:
        yield sample
    finally:
        sample.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if enforce and budget > 0 and not sample.ok:
            raise PerformanceBudgetError(
                f"{name} took {sample.elapsed_ms:.2f}ms > budget {budget}ms"
            )


def assert_within_budget(name: str, elapsed_ms: float) -> None:
    budget = BUDGETS.get(name)
    if budget is None:
        return
    if elapsed_ms > budget:
        raise PerformanceBudgetError(
            f"{name} took {elapsed_ms:.2f}ms > budget {budget}ms"
        )
