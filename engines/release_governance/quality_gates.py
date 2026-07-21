"""P17.2 — Quality Gates vs Golden Release."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.perf_budgets import BUDGETS
from engines.release_governance.golden_release import (
    GoldenRelease,
    QualityMetrics,
    load_golden_release,
    measure_candidate_quality,
)


@dataclass
class GateResult:
    name: str
    ok: bool
    detail: str = ""
    baseline: Any = None
    candidate: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "baseline": self.baseline,
            "candidate": self.candidate,
        }


@dataclass
class QualityGatesReport:
    ok: bool
    gates: list[GateResult] = field(default_factory=list)
    blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "gates": [g.to_dict() for g in self.gates],
        }


def evaluate_quality_gates(
    *,
    candidate: QualityMetrics | None = None,
    golden: GoldenRelease | None = None,
    root=None,
    max_processing_ms: float | None = None,
) -> QualityGatesReport:
    """
    Release is forbidden if any gate fails:
      - translation quality regression
      - overlap / overflow increase
      - sync degradation
      - processing time over budget
      - determinism collapse
      - new Runtime Integrity critical errors
    """
    golden = golden or load_golden_release(root=root)
    candidate = candidate or measure_candidate_quality()
    gates: list[GateResult] = []

    if golden is None:
        # First release: establish baseline without blocking (promote separately).
        gates.append(
            GateResult(
                "golden_baseline",
                ok=True,
                detail="no golden yet — candidate accepted as bootstrap",
                candidate=candidate.to_dict(),
            )
        )
        return QualityGatesReport(ok=True, gates=gates, blocked=False)

    base = golden.metrics

    gates.append(
        GateResult(
            "translation_quality",
            ok=candidate.translation_quality_score >= base.translation_quality_score - 1e-9,
            detail="score must not drop vs golden",
            baseline=base.translation_quality_score,
            candidate=candidate.translation_quality_score,
        )
    )
    gates.append(
        GateResult(
            "overlap",
            ok=candidate.overlap_count <= base.overlap_count,
            detail="overlap must not increase",
            baseline=base.overlap_count,
            candidate=candidate.overlap_count,
        )
    )
    gates.append(
        GateResult(
            "overflow",
            ok=candidate.overflow_count <= base.overflow_count,
            detail="overflow must not increase",
            baseline=base.overflow_count,
            candidate=candidate.overflow_count,
        )
    )
    gates.append(
        GateResult(
            "synchronization",
            ok=candidate.sync_score >= base.sync_score - 1e-9,
            detail="sync_score must not degrade",
            baseline=base.sync_score,
            candidate=candidate.sync_score,
        )
    )

    budget_cap = max_processing_ms
    if budget_cap is None:
        # Soft wall-clock budget for synthetic batch (not per-call scheduler budget).
        budget_cap = max(500.0, float(BUDGETS.get("alignment", 50.0)) * max(1, candidate.segment_count))
    gates.append(
        GateResult(
            "processing_budget",
            ok=candidate.processing_ms <= budget_cap,
            detail=f"processing_ms <= {budget_cap}",
            baseline=budget_cap,
            candidate=candidate.processing_ms,
        )
    )

    # Determinism: re-measure same synthetic settings → same fingerprint
    again = measure_candidate_quality(settings={"p17": True})
    det_ok = (
        bool(candidate.deterministic_fingerprint)
        and candidate.deterministic_fingerprint == again.deterministic_fingerprint
    )
    gates.append(
        GateResult(
            "determinism",
            ok=det_ok,
            detail="fingerprint stable across two runs",
            baseline=candidate.deterministic_fingerprint,
            candidate=again.deterministic_fingerprint,
        )
    )

    gates.append(
        GateResult(
            "runtime_integrity",
            ok=candidate.runtime_integrity_errors <= base.runtime_integrity_errors,
            detail="no new RUNTIME_INTEGRITY critical errors",
            baseline=base.runtime_integrity_errors,
            candidate=candidate.runtime_integrity_errors,
        )
    )

    ok = all(g.ok for g in gates)
    return QualityGatesReport(ok=ok, gates=gates, blocked=not ok)
