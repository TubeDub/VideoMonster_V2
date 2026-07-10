"""Persist stress test history for version comparison."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from engines.stress_test.config import history_dir


def save_history_entry(batch: dict[str, Any], *, app_dir: Path) -> str:
    hist = history_dir(app_dir)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    version = str(batch.get("version") or "unknown")
    fname = f"{stamp}_v{version.replace('.', '-')}.json"
    path = hist / fname

    results = batch.get("results") or []
    summary = batch.get("summary") or {}
    entry = {
        "batch_id": batch.get("batch_id"),
        "version": version,
        "started_at": batch.get("started_at"),
        "finished_at": batch.get("finished_at"),
        "total": batch.get("total"),
        "passed": batch.get("passed"),
        "failed": batch.get("failed"),
        "avg_quality": summary.get("avg_quality"),
        "avg_duration_sec": summary.get("avg_duration_sec"),
        "elapsed_sec": summary.get("elapsed_sec"),
        "report_html": batch.get("report_html"),
        "report_txt": batch.get("report_txt"),
        "videos": [
            {
                "video": r.get("video"),
                "passed": r.get("passed"),
                "avg_quality": r.get("avg_quality"),
                "duration_sec": r.get("duration_sec"),
                "issue_count": len(r.get("issues") or []),
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    index_path = hist / "index.json"
    index: list[dict[str, Any]] = []
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index = []
    index.insert(
        0,
        {
            "file": fname,
            "version": version,
            "stamp": stamp,
            "passed": entry["passed"],
            "failed": entry["failed"],
            "total": entry["total"],
            "avg_quality": entry["avg_quality"],
        },
    )
    index = index[:50]
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)
