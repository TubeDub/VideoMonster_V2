"""AI Core 4.2 — streaming_pipeline_report.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).resolve().parents[3]


def write_streaming_pipeline_report(
    task_id: str,
    payload: dict[str, Any],
    *,
    app_dir: Path | None = None,
) -> Path:
    base = app_dir or _APP_DIR
    diag = base / "output" / "diagnostics" / task_id
    diag.mkdir(parents=True, exist_ok=True)
    path = diag / "streaming_pipeline_report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from engines.open_ddf import open_ddf

        summary = payload.get("summary") or {}
        open_ddf.record_agent(
            task_id,
            "StreamingPipeline/4.2",
            called=True,
            success=bool(summary.get("success", True)),
            output_metrics=summary,
        )
        open_ddf.save(task_id)
    except Exception:
        pass

    return path
