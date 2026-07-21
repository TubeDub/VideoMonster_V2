"""RASM analyze façade — full project sync analysis + report write."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.rasm.config import load_rasm_settings
from engines.rasm.hooks import run_rasm_hooks
from engines.rasm.metrics import analyze_segments, compute_stats
from engines.rasm.reports import write_sync_reports


def analyze_project(
    task_id: str,
    segments: list[dict[str, Any]],
    *,
    app_dir: Path | None = None,
    info: dict[str, Any] | None = None,
    write_reports: bool = True,
    apply_hooks: bool = True,
) -> dict[str, Any]:
    root = Path(app_dir) if app_dir else Path(__file__).resolve().parents[2]
    settings = load_rasm_settings(root)
    rows = analyze_segments(segments, settings=settings)
    stats = compute_stats(rows)
    hooks: dict[str, Any] = {}
    if apply_hooks:
        hooks = run_rasm_hooks(segments, info=info, settings=settings)

    report_meta: dict[str, Any] = {}
    if write_reports:
        report_meta = write_sync_reports(
            task_id, segments, app_dir=root, settings=settings
        )

    return {
        "ok": True,
        "task_id": Path(task_id).name,
        "phase": "R5",
        "stats": stats,
        "segments": [r.to_dict() for r in rows],
        "hooks": hooks,
        "reports": report_meta.get("paths") if report_meta else {},
        "settings": settings.to_dict(),
    }
