"""TQE analytics + failure/dataset persistence + regression."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from engines.tqe.models import SegmentQualityDecision, TQEBatchResult


def quality_root(app_dir: Path | str) -> Path:
    root = Path(app_dir) / "quality"
    for sub in (
        "reports",
        "failures",
        "analytics",
        "datasets/accepted",
        "datasets/rejected",
        "datasets/manual_fixed",
        "datasets/llm_fixed",
        "datasets/gold_standard",
        "retries",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def build_analytics(result: TQEBatchResult) -> dict[str, Any]:
    entity_loss = meaning_loss = grammar_err = timing_err = narrative_err = 0
    llm_used = 0
    review_ms = 0.0
    confs: list[float] = []
    for d in result.decisions:
        confs.append(d.overall_confidence)
        for r in d.reports:
            review_ms += float(r.review_time_ms or 0)
            if r.llm_used:
                llm_used += 1
            for e in r.errors:
                code = str(e.get("code") or "")
                if "entity" in code or "preserved" in code or "number" in code:
                    entity_loss += 1
                elif "meaning" in code or "event" in code or "negation" in code:
                    meaning_loss += 1
                elif "grammar" in code or "orphan" in code or "incomplete" in code:
                    grammar_err += 1
                elif "timing" in code or "slot" in code or "compress" in code:
                    timing_err += 1
                elif "narrative" in code:
                    narrative_err += 1
    total = max(len(result.decisions), 1)
    return {
        "segments_total": len(result.decisions),
        "passed": result.passed,
        "rejected": result.rejected,
        "retry_count": sum(1 for d in result.decisions if d.retry_strategy != "none"),
        "retry_average": round(
            sum(1 for d in result.decisions if d.retry_strategy != "none") / total, 3
        ),
        "average_review_time_ms": round(review_ms / total, 2),
        "average_confidence": round(sum(confs) / len(confs), 4) if confs else 0.0,
        "entity_loss": entity_loss,
        "meaning_loss": meaning_loss,
        "grammar_errors": grammar_err,
        "timing_errors": timing_err,
        "narrative_errors": narrative_err,
        "llm_usage": llm_used,
        "retry_success_rate": 0.0,
        "fallback_usage": sum(
            1 for d in result.decisions for r in d.reports if r.fallback_used
        ),
        "gate_passed": result.gate_passed,
    }


def persist_failure(
    app_dir: Path | str,
    decision: SegmentQualityDecision,
    *,
    task_id: str,
    final_status: str = "REJECT",
    retry_history: list[dict] | None = None,
) -> Path:
    root = quality_root(app_dir)
    stamp = int(time.time() * 1000)
    path = root / "failures" / f"{task_id}_{decision.index:04d}_{stamp}.json"
    payload = {
        "task_id": task_id,
        "index": decision.index,
        "original": decision.original,
        "translation": decision.translation,
        "reason": decision.explanation,
        "reviewers": [r.to_dict() for r in decision.reports],
        "confidence": decision.overall_confidence,
        "retry_history": list(retry_history or []),
        "final_status": final_status,
        "retry_strategy": decision.retry_strategy,
        "ts": stamp,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # dataset copy
    ds = root / "datasets" / "rejected" / path.name
    ds.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def persist_accepted(
    app_dir: Path | str,
    decision: SegmentQualityDecision,
    *,
    task_id: str,
) -> Path:
    root = quality_root(app_dir)
    path = root / "datasets" / "accepted" / f"{task_id}_{decision.index:04d}.json"
    path.write_text(
        json.dumps(decision.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def persist_batch_report(app_dir: Path | str, result: TQEBatchResult) -> Path:
    root = quality_root(app_dir)
    path = root / "reports" / f"tqe_{result.task_id}_{int(time.time())}.json"
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    analytics_path = root / "analytics" / f"analytics_{result.task_id}.json"
    analytics_path.write_text(
        json.dumps(result.analytics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def check_regression(
    app_dir: Path | str,
    analytics: dict[str, Any],
    *,
    baseline_name: str = "baseline.json",
) -> dict[str, Any]:
    """Compare current analytics to last baseline; report silent quality drops."""
    root = quality_root(app_dir)
    baseline_path = root / "analytics" / baseline_name
    report: dict[str, Any] = {"regressed": False, "drops": [], "baseline": None}
    if not baseline_path.is_file():
        baseline_path.write_text(
            json.dumps(analytics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report["baseline"] = "created"
        return report

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception:
        return report

    keys = (
        ("average_confidence", True),
        ("entity_loss", False),
        ("meaning_loss", False),
        ("grammar_errors", False),
        ("timing_errors", False),
        ("narrative_errors", False),
    )
    for key, higher_is_better in keys:
        cur = float(analytics.get(key) or 0)
        base = float(baseline.get(key) or 0)
        if higher_is_better:
            if base > 0 and cur < base * 0.95:
                report["drops"].append(
                    {"metric": key, "baseline": base, "current": cur}
                )
        else:
            if cur > base * 1.15 + 1:
                report["drops"].append(
                    {"metric": key, "baseline": base, "current": cur}
                )
    report["regressed"] = bool(report["drops"])
    report["baseline"] = baseline
    # Always refresh rolling baseline lightly when not regressed
    if not report["regressed"]:
        baseline_path.write_text(
            json.dumps(analytics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report
