"""P16.3 — Concurrency harness (projects / TTS / Scheduler / UUID)."""

from __future__ import annotations

import concurrent.futures
import uuid
from dataclasses import dataclass
from typing import Any

from engines.pipeline_integrity.uuid_chain import ensure_all_uuids, ensure_tts_uuid
from engines.scheduler import Scheduler


@dataclass
class ConcurrencyResult:
    ok: bool
    projects: int
    uuid_unique: bool
    scheduler_ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "projects": self.projects,
            "uuid_unique": self.uuid_unique,
            "scheduler_ok": self.scheduler_ok,
            "detail": self.detail,
        }


def _project_worker(project_idx: int, segments_per_project: int) -> dict[str, Any]:
    rows = []
    sched = Scheduler()
    for i in range(segments_per_project):
        seg = {
            "segment_id": uuid.uuid4().hex,
            "index": i,
            "translated_text": f"p{project_idx}-{i}",
            "start_ms": i * 1000,
            "end_ms": i * 1000 + 800,
        }
        ensure_all_uuids(seg)
        ensure_tts_uuid(seg, force_new=True)
        sched.update_time(
            [seg],
            seg["segment_id"],
            start_ms=seg["start_ms"],
            end_ms=seg["end_ms"] + 1,
        )
        rows.append(seg)
    return {
        "project": project_idx,
        "segment_ids": [r["segment_id"] for r in rows],
        "tts_uuids": [r["tts_uuid"] for r in rows],
        "ok": True,
    }


def run_concurrency_harness(
    *,
    projects: int = 8,
    segments_per_project: int = 20,
    workers: int = 4,
) -> ConcurrencyResult:
    """Parallel projects + scheduler updates; assert no duplicate UUIDs / deadlocks."""
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [
            pool.submit(_project_worker, p, segments_per_project) for p in range(projects)
        ]
        for fut in concurrent.futures.as_completed(futs, timeout=120):
            results.append(fut.result())

    all_seg = []
    all_tts = []
    for r in results:
        all_seg.extend(r["segment_ids"])
        all_tts.extend(r["tts_uuids"])

    uuid_unique = len(all_seg) == len(set(all_seg)) and len(all_tts) == len(set(all_tts))
    scheduler_ok = all(r.get("ok") for r in results)
    ok = uuid_unique and scheduler_ok and len(results) == projects
    return ConcurrencyResult(
        ok=ok,
        projects=projects,
        uuid_unique=uuid_unique,
        scheduler_ok=scheduler_ok,
        detail=f"segments={len(all_seg)} tts_uuids={len(all_tts)}",
    )
