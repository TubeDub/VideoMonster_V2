"""Development lifecycle tracker (TZ #1 §4)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

STAGE_PLANNING = "planning"
STAGE_DEVELOPMENT = "development"
STAGE_AUTO_CHECK = "auto_check"
STAGE_REVIEWER = "reviewer"
STAGE_ACCEPTED = "accepted"
STAGE_MERGE = "merge"

_LIFECYCLE_ORDER = (
    STAGE_PLANNING,
    STAGE_DEVELOPMENT,
    STAGE_AUTO_CHECK,
    STAGE_REVIEWER,
    STAGE_ACCEPTED,
    STAGE_MERGE,
)

_APP_DIR = Path(__file__).resolve().parents[2]


def _path(run_id: str, app_dir: Path | None = None) -> Path:
    root = app_dir or _APP_DIR
    return root / "output" / "diagnostics" / run_id / "development_lifecycle.json"


def record_stage(
    run_id: str,
    stage: str,
    *,
    detail: str = "",
    ok: bool = True,
    app_dir: Path | None = None,
) -> dict[str, Any]:
    path = _path(run_id, app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"run_id": run_id, "stages": []}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    entry = {
        "stage": stage,
        "ok": ok,
        "detail": detail,
        "ts": time.time(),
    }
    stages = list(data.get("stages") or [])
    stages.append(entry)
    data["stages"] = stages
    data["current_stage"] = stage
    data["completed"] = stage == STAGE_MERGE and ok
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_lifecycle(run_id: str, app_dir: Path | None = None) -> dict[str, Any]:
    path = _path(run_id, app_dir)
    if not path.is_file():
        return {"run_id": run_id, "stages": [], "current_stage": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"run_id": run_id, "stages": [], "current_stage": None}


def advance_pipeline_lifecycle(run_id: str, *, phase: str, app_dir: Path | None = None) -> None:
    """Map orchestrator phases to lifecycle stages."""
    mapping = {
        "planner": STAGE_PLANNING,
        "pipeline_start": STAGE_DEVELOPMENT,
        "peer_validation": STAGE_AUTO_CHECK,
        "reviewer": STAGE_REVIEWER,
        "pipeline_done": STAGE_ACCEPTED,
        "mix": STAGE_MERGE,
    }
    stage = mapping.get(phase, STAGE_DEVELOPMENT)
    record_stage(run_id, stage, detail=phase, app_dir=app_dir)
