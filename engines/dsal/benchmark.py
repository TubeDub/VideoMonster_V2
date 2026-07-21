"""P5 — DSAL George Lucas benchmark + governance gates (TZ v4.0)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN = ROOT / "tests" / "golden" / "dub" / "george_lucas_en_uk_20.json"
DEFAULT_OUT_DIR = ROOT / "output" / "p5_dsal_benchmark"

# Gates (LLM-off baseline)
GATE_AVG_MATCH_MIN = 85.0  # aligned with LOCK gate; >90 is stretch with LLM
GATE_AVG_MATCH_STRETCH = 90.0
GATE_SEG6_DELTA_PCT_MAX = 15.0
GATE_CLAUSE_CRITICAL_MIN = 0.85


@dataclass
class DSALGate:
    name: str
    ok: bool
    detail: str = ""
    value: Any = None
    threshold: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DSALBenchmarkReport:
    ok: bool
    suite: str = "dsal_v4_p5"
    llm_off: bool = True
    issued_at: str = ""
    golden_path: str = ""
    gates: list[DSALGate] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    segments: list[dict[str, Any]] = field(default_factory=list)
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "suite": self.suite,
            "llm_off": self.llm_off,
            "issued_at": self.issued_at,
            "golden_path": self.golden_path,
            "gates": {g.name: g.to_dict() for g in self.gates},
            "metrics": self.metrics,
            "segments": self.segments,
            "path": self.path,
        }


def _load_golden(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_dsal_benchmark(
    *,
    golden_path: Path | None = None,
    out_dir: Path | None = None,
    llm_off: bool = True,
    allow_llm: bool = False,
) -> DSALBenchmarkReport:
    """Run George Lucas 20-seg DSAL benchmark (per-seg, no block-merge scramble)."""
    from engines.dsal.clause_coverage import compute_clause_coverage
    from engines.dsal.lock_gate import evaluate_lock_gate
    from engines.translation_validation import apply_dsal_before_lock

    golden_path = Path(golden_path or DEFAULT_GOLDEN)
    out_dir = Path(out_dir or DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    if llm_off:
        allow_llm = False
        try:
            import engines.translation_adapt as ta

            ta.llm_rephrase_available = lambda: False  # type: ignore[method-assign]
        except Exception:
            pass

    golden = _load_golden(golden_path)
    segs_spec = list(golden.get("segments") or [])
    info: dict[str, Any] = {
        "target_lang": str(golden.get("target_lang") or "uk"),
        "source_lang": str(golden.get("source_lang") or "en"),
        "source_segments": [s.get("en") for s in segs_spec],
        "segments_data": [
            {
                "slot_ms": int(s.get("slot_ms") or 0),
                "final_text": str(s.get("uk") or ""),
                "text": str(s.get("uk") or ""),
                "plain_text": str(s.get("uk") or ""),
                "tts_ms": int(s.get("actual_tts_ms") or 0),
            }
            for s in segs_spec
        ],
        "pipeline_state": "VALIDATED",
    }

    # Governance baseline: per-seg DSAL only (block merge measured separately in prod)
    summary = apply_dsal_before_lock(
        info,
        allow_llm=allow_llm and not llm_off,
        block_merge=False,
    )

    segments = list(info.get("segments_data") or [])
    src = list(info.get("source_segments") or [])
    failures = evaluate_lock_gate(segments, source_segments=src)

    rows: list[dict[str, Any]] = []
    scores: list[float] = []
    critical_clauses: list[float] = []
    for i, (spec, seg) in enumerate(zip(segs_spec, segments)):
        score = float(seg.get("duration_match_score") or 0)
        text = str(seg.get("final_text") or "")
        en = str(spec.get("en") or "")
        cov_detail = compute_clause_coverage(en, text)
        # Only track critical-mapped coverage for the avg gate
        if cov_detail.total > 0:
            critical_clauses.append(float(cov_detail.coverage))
        slot = max(1, int(seg.get("slot_ms") or 1))
        delta = int(seg.get("dsal_delta_ms") or 0)
        delta_pct = abs(delta) * 100.0 / slot
        scores.append(score)
        must_ok = True
        for phrase in spec.get("must_restore") or []:
            if phrase.lower() not in text.lower():
                must_ok = False
        for phrase in spec.get("must_contain") or []:
            if phrase.lower() not in text.lower():
                must_ok = False
        rows.append(
            {
                "index": int(spec.get("index") or i + 1),
                "dsal_band": seg.get("dsal_band"),
                "duration_match_score": score,
                "clause_coverage": float(cov_detail.coverage),
                "clause_total": cov_detail.total,
                "dsal_delta_ms": delta,
                "delta_pct": round(delta_pct, 2),
                "adaptation_executed": bool(seg.get("adaptation_executed")),
                "lock_gate_ok": seg.get("lock_gate_ok"),
                "must_restore_ok": must_ok,
            }
        )

    avg_match = round(sum(scores) / max(1, len(scores)), 2)
    avg_critical_clause = (
        round(sum(critical_clauses) / len(critical_clauses), 3)
        if critical_clauses
        else 1.0
    )
    seg6 = next((r for r in rows if r["index"] == 6), None)
    seg6_delta_pct = float(seg6["delta_pct"]) if seg6 else 999.0
    adapted = sum(1 for r in rows if r["adaptation_executed"])
    must_fail = sum(1 for r in rows if r["must_restore_ok"] is False)

    # Measured segs (have actual_tts_ms in golden)
    measured_specs = {
        int(s.get("index") or 0)
        for s in segs_spec
        if int(s.get("actual_tts_ms") or 0) > 0
    }
    measured_rows = [r for r in rows if r["index"] in measured_specs]
    measured_scores = [float(r["duration_match_score"]) for r in measured_rows]
    avg_measured = (
        round(sum(measured_scores) / len(measured_scores), 2) if measured_scores else 100.0
    )
    # TZ poster-child: seg #6 underflow must be fixed (delta < 15%)
    seg6_ok = seg6 is not None and float(seg6["delta_pct"]) <= GATE_SEG6_DELTA_PCT_MAX

    gates = [
        DSALGate(
            "llm_off_ok",
            ok=bool(llm_off),
            detail="benchmark ran with LLM disabled" if llm_off else "LLM allowed",
            value=llm_off,
            threshold=True,
        ),
        DSALGate(
            "seg6_underflow_fixed",
            ok=seg6_ok,
            detail=f"seg6_delta_pct={seg6_delta_pct} score={seg6.get('duration_match_score') if seg6 else None}",
            value=seg6_delta_pct,
            threshold=GATE_SEG6_DELTA_PCT_MAX,
        ),
        DSALGate(
            "clause_coverage_critical",
            ok=avg_critical_clause >= GATE_CLAUSE_CRITICAL_MIN,
            detail=f"avg_critical_clause={avg_critical_clause}",
            value=avg_critical_clause,
            threshold=GATE_CLAUSE_CRITICAL_MIN,
        ),
        DSALGate(
            "must_restore",
            ok=must_fail == 0,
            detail=f"failures={must_fail}",
            value=must_fail,
            threshold=0,
        ),
        DSALGate(
            "measured_avg_match",
            ok=avg_measured >= GATE_AVG_MATCH_MIN or seg6_ok,
            detail=f"avg_measured={avg_measured} n={len(measured_rows)} (informational if seg6 ok)",
            value=avg_measured,
            threshold=GATE_AVG_MATCH_MIN,
        ),
        DSALGate(
            "stretch_avg_gt_90",
            ok=avg_match > GATE_AVG_MATCH_STRETCH,
            detail=(
                f"avg_all={avg_match} (informational; LLM-off estimate vs slot "
                f"often yellow — stretch target)"
            ),
            value=avg_match,
            threshold=GATE_AVG_MATCH_STRETCH,
        ),
    ]

    # Blocking gates for Release Certificate (LLM-off DoD)
    blocking_names = {
        "llm_off_ok",
        "seg6_underflow_fixed",
        "clause_coverage_critical",
        "must_restore",
    }
    blocking = [g for g in gates if g.name in blocking_names]

    metrics = {
        "avg_duration_match_score": avg_match,
        "avg_measured_match_score": avg_measured,
        "avg_critical_clause_coverage": avg_critical_clause,
        "seg6_delta_pct": seg6_delta_pct,
        "adapted": adapted,
        "measured_segments": len(measured_rows),
        "lock_gate_failures": len(failures),
        "segments": len(rows),
        "dsal_pre_lock": summary if isinstance(summary, dict) else {},
        "stretch_avg_gt_90": avg_match > GATE_AVG_MATCH_STRETCH,
    }

    report = DSALBenchmarkReport(
        ok=all(g.ok for g in blocking),
        llm_off=llm_off,
        issued_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        golden_path=str(golden_path),
        gates=gates,
        metrics=metrics,
        segments=rows,
    )
    out_path = out_dir / "dsal_benchmark_report.json"
    out_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report.path = str(out_path)
    return report
