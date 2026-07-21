"""Offline pre-merge audit from existing studio session (no pipeline run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.pipeline_integrity.timing_lifecycle_audit import dump_pre_merge_timing_audit

TASK = "66651127645a43958106ccd4d700fb9c"
studio = ROOT / "output" / "studio_sessions" / f"{TASK}.json"
if not studio.is_file():
    print("missing", studio)
    raise SystemExit(1)

data = json.loads(studio.read_text(encoding="utf-8"))
segs = data.get("segments") or []
timing_map = data.get("timing_map") or data.get("timing_map_backup") or []
report = dump_pre_merge_timing_audit(
    segs,
    task_id=TASK,
    timing_map=timing_map,
    source="offline_studio_replay",
)
print(json.dumps(report.get("summary") or {}, indent=2, ensure_ascii=False))
