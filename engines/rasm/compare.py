"""RASM R5 — minimal Before/After compare of two sync_report.json payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _stats(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload.get("stats") or {})


def compare_sync_reports(
    before: dict[str, Any] | str | Path,
    after: dict[str, Any] | str | Path,
) -> dict[str, Any]:
    """Return summary deltas between two sync reports."""

    def _load(x: dict[str, Any] | str | Path) -> dict[str, Any]:
        if isinstance(x, dict):
            return x
        return json.loads(Path(x).read_text(encoding="utf-8"))

    a = _load(before)
    b = _load(after)
    sa, sb = _stats(a), _stats(b)

    keys = [
        "segments_total",
        "green",
        "yellow",
        "red",
        "avg_reserve_ms",
        "avg_overflow_ms",
        "max_overflow_ms",
        "max_early_ms",
        "sync_fail_count",
        "sync_warning_count",
    ]
    deltas: dict[str, Any] = {}
    for k in keys:
        va, vb = sa.get(k), sb.get(k)
        try:
            deltas[k] = {
                "before": va,
                "after": vb,
                "delta": (float(vb) - float(va)) if va is not None and vb is not None else None,
            }
        except (TypeError, ValueError):
            deltas[k] = {"before": va, "after": vb, "delta": None}

    return {
        "ok": True,
        "before_task_id": a.get("task_id"),
        "after_task_id": b.get("task_id"),
        "deltas": deltas,
        "summary": {
            "overflow_count_before": sa.get("red"),
            "overflow_count_after": sb.get("red"),
            "avg_reserve_before": sa.get("avg_reserve_ms"),
            "avg_reserve_after": sb.get("avg_reserve_ms"),
            "improved": (sb.get("red") or 0) < (sa.get("red") or 0),
        },
    }
