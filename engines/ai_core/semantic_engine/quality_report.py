"""Write semantic_quality_report.json for OpenDDF."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).resolve().parents[3]


def write_semantic_quality_report(
    task_id: str,
    segments_log: list[dict[str, Any]],
    *,
    summary: dict[str, Any],
    project_uuid: str = "",
    app_dir: Path | None = None,
) -> Path:
    base = app_dir or _APP_DIR
    payload = {
        "task_id": task_id,
        "project_uuid": project_uuid,
        "engine": "Translation+Semantic v4.0",
        "summary": summary,
        "segment_count": len(segments_log),
        "segments": segments_log,
    }
    diag = base / "output" / "diagnostics" / task_id
    diag.mkdir(parents=True, exist_ok=True)
    path = diag / "semantic_quality_report.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    if project_uuid:
        man = base / "output" / "manifests" / project_uuid
        man.mkdir(parents=True, exist_ok=True)
        with open(man / "semantic_quality_report.json", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path
