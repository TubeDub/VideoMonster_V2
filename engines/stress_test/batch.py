"""Run full stress test batch with progress tracking."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from engines.app_version import APP_VERSION
from engines.stress_test.checks import analyze_task_result
from engines.stress_test.config import app_dir, logs_dir
from engines.stress_test.discovery import ensure_sample_hint, list_test_videos
from engines.stress_test.history import save_history_entry
from engines.stress_test.pipeline_runner import (
    build_review,
    collect_log_paths,
    get_task_snapshot,
    start_dub_task,
    wait_for_task,
)
from engines.stress_test.report import write_stress_reports

BATCHES: dict[str, dict[str, Any]] = {}
BATCH_LOCK = threading.Lock()


def _safe_relative(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _update_batch(batch_id: str, **fields: Any) -> None:
    with BATCH_LOCK:
        batch = BATCHES.get(batch_id)
        if not batch:
            return
        batch.update(fields)


def get_batch(batch_id: str) -> dict[str, Any] | None:
    with BATCH_LOCK:
        b = BATCHES.get(batch_id)
        return dict(b) if b else None


def start_batch(*, base: Path | None = None) -> dict[str, Any]:
    root = app_dir(base)
    ensure_sample_hint(root)
    videos = list_test_videos(root)
    batch_id = uuid.uuid4().hex[:12]

    batch: dict[str, Any] = {
        "batch_id": batch_id,
        "status": "running",
        "version": APP_VERSION,
        "started_at": time.time(),
        "finished_at": None,
        "total": len(videos),
        "current_index": 0,
        "current_video": "",
        "current_stage": "",
        "passed": 0,
        "failed": 0,
        "errors_count": 0,
        "results": [],
        "eta_sec": None,
        "report_html": None,
        "report_txt": None,
        "message": "",
    }

    with BATCH_LOCK:
        BATCHES[batch_id] = batch

    if not videos:
        batch["status"] = "done"
        batch["message"] = "Нет видео в data/stress_tests/"
        write_stress_reports(batch, app_dir=root)
        save_history_entry(batch, app_dir=root)
        return dict(batch)

    t = threading.Thread(target=_run_batch, args=(batch_id, root, videos), daemon=True)
    t.start()
    return dict(batch)


def _run_batch(batch_id: str, root: Path, videos: list[dict[str, Any]]) -> None:
    results: list[dict[str, Any]] = []
    durations: list[float] = []
    t0 = time.time()

    for idx, video in enumerate(videos):
        name = video["name"]
        _update_batch(
            batch_id,
            current_index=idx + 1,
            current_video=name,
            current_stage="starting",
            eta_sec=_estimate_eta(durations, idx, len(videos)),
        )

        item: dict[str, Any] = {
            "video": name,
            "path": video["path"],
            "task_id": None,
            "passed": False,
            "duration_sec": 0.0,
            "issues": [],
            "stages": {},
            "avg_quality": None,
            "log_paths": [],
        }
        vstart = time.time()

        try:
            task_id = start_dub_task(
                video_path=video["path"],
                app_dir=root,
                target_lang=str(video.get("target_lang") or "uk"),
                source_lang=str(video.get("source_lang") or "en"),
                voice=str(video.get("voice") or "uk-UA-OstapNeural"),
                model_size=str(video.get("model_size") or "tiny"),
                dub_style=str(video.get("dub_style") or "modern"),
                translation_review_before_tts=bool(
                    video.get("translation_review_before_tts", False)
                ),
                mix_volume=float(video.get("mix_volume") or 0.3),
            )
            item["task_id"] = task_id

            def _tick(st: dict[str, Any]) -> None:
                _update_batch(batch_id, current_stage=str(st.get("step") or ""))

            final = wait_for_task(task_id, on_tick=_tick)
            task, info = get_task_snapshot(task_id)
            review = build_review(info)
            output_name = task.get("output_file")
            output_path = root / "output" / output_name if output_name else None
            log_paths = collect_log_paths(info, task_id, root)
            per_test_log = logs_dir(root) / f"{batch_id}_{Path(name).stem}.log"
            _write_test_log(per_test_log, task, info, final)

            analysis = analyze_task_result(
                task=task,
                info=info,
                review=review,
                output_path=output_path,
                log_paths=log_paths + [str(per_test_log)],
                app_dir=root,
            )
            item.update(analysis)
            item["duration_sec"] = round(time.time() - vstart, 2)
            item["log_paths"] = [str(per_test_log.relative_to(root))] + [
                str(Path(p).relative_to(root)) if _safe_relative(Path(p), root) else p
                for p in log_paths
            ]
            item["output_file"] = output_name

            if final.get("status") == "timeout":
                item["passed"] = False
                item["issues"].append({"code": "timeout", "severity": "critical"})

        except Exception as exc:
            item["passed"] = False
            item["issues"].append(
                {"code": "exception", "severity": "critical", "detail": str(exc)}
            )
            item["duration_sec"] = round(time.time() - vstart, 2)

        results.append(item)
        durations.append(item["duration_sec"])
        passed = sum(1 for r in results if r.get("passed"))
        failed = len(results) - passed
        err_count = sum(
            1 for r in results for i in (r.get("issues") or []) if i.get("severity") == "critical"
        )
        _update_batch(
            batch_id,
            results=list(results),
            passed=passed,
            failed=failed,
            errors_count=err_count,
            current_stage="idle" if idx + 1 >= len(videos) else "next",
        )

    elapsed = round(time.time() - t0, 2)
    quality_vals = [r["avg_quality"] for r in results if r.get("avg_quality") is not None]
    summary = {
        "elapsed_sec": elapsed,
        "avg_duration_sec": round(sum(durations) / len(durations), 2) if durations else 0,
        "avg_quality": round(sum(quality_vals) / len(quality_vals), 1) if quality_vals else None,
    }

    with BATCH_LOCK:
        batch = BATCHES[batch_id]
        batch["status"] = "done"
        batch["finished_at"] = time.time()
        batch["results"] = results
        batch["summary"] = summary
        batch["current_stage"] = "done"
        batch["eta_sec"] = 0

    paths = write_stress_reports(batch, app_dir=root)
    save_history_entry(batch, app_dir=root)
    _update_batch(batch_id, report_html=paths.get("html"), report_txt=paths.get("txt"))


def _estimate_eta(durations: list[float], done: int, total: int) -> int | None:
    if not durations or done >= total:
        return 0 if done >= total else None
    avg = sum(durations) / len(durations)
    remaining = total - done
    return int(avg * remaining)


def _write_test_log(path: Path, task: dict, info: dict, poll: dict) -> None:
    lines = [
        f"status={task.get('status')} step={task.get('step')}",
        f"poll={json.dumps(poll, ensure_ascii=False)}",
        f"errors={task.get('errors')}",
        f"segments={len(info.get('segments_data') or [])}",
        f"detected_lang={info.get('detected_lang')}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
