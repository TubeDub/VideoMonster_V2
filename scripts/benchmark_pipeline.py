#!/usr/bin/env python3
"""TubeDub pipeline benchmark — diagnostics only (TZ: no blind optimizations).

Usage:
  python scripts/benchmark_pipeline.py
  VM_PERF_PROFILE=1 python scripts/benchmark_pipeline.py
  VM_PERF_DEBUG=1 python scripts/benchmark_pipeline.py

Produces per run:
  output/diagnostics/<task_id>/performance_report.json
  output/diagnostics/<task_id>/timeline.json
  output/dev/cprofile_<task_id>.prof   (when VM_PERF_PROFILE=1)
"""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TubeDub performance benchmark (diagnostics)")
    p.add_argument("--video", help="Override benchmark video path")
    p.add_argument("--target-lang", default="uk")
    p.add_argument("--source-lang", default="en")
    p.add_argument("--timeout", type=int, default=7200, help="Max wait seconds")
    p.add_argument("--profile", action="store_true", help="Enable cProfile (or VM_PERF_PROFILE=1)")
    p.add_argument("--debug-artifacts", action="store_true", help="Rich JSON (or VM_PERF_DEBUG=1)")
    return p.parse_args()


def _run_dub(client, video_path: Path, target_lang: str, source_lang: str) -> str:
    from werkzeug.datastructures import FileStorage

    with video_path.open("rb") as fh:
        data = {
            "target_lang": target_lang,
            "source_lang": source_lang,
            "model_size": "tiny",
            "dub_style": "modern",
        }
        r = client.post(
            "/api/auto_dub/start",
            data={**data, "video": (FileStorage(fh, filename=video_path.name))},
            content_type="multipart/form-data",
        )
    if r.status_code not in (200, 202):
        raise RuntimeError(f"start failed: {r.status_code} {r.get_data(as_text=True)[:500]}")
    body = r.get_json() or {}
    task_id = body.get("task_id")
    if not task_id:
        raise RuntimeError(f"no task_id in response: {body}")
    return str(task_id)


def _wait_done(client, task_id: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/auto_dub/status/{task_id}?lang=ru&lite=1")
        data = r.get_json() or {}
        status = data.get("status")
        if status in ("done", "error", "cancelled"):
            return data
        time.sleep(2)
    raise TimeoutError(f"task {task_id} not finished within {timeout}s")


def _print_summary(task_id: str, paths: dict[str, str]) -> None:
    report_path = Path(paths.get("performance_report_json", ""))
    if not report_path.is_file():
        print("No performance_report.json")
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print("\n=== Performance summary ===")
    print(f"task_id:     {task_id}")
    print(f"total_sec:   {report.get('total_sec')}")
    print(f"success:     {report.get('success')}")
    bn = report.get("bottleneck") or {}
    if bn:
        flag = " *** BOTTLENECK >50%" if bn.get("exceeds_threshold") else ""
        print(
            f"slowest:     {bn.get('stage')} = {bn.get('duration_sec')}s "
            f"({bn.get('percent_of_total')}%){flag}"
        )
    print("\nStages (sec / %):")
    stages = report.get("stages_sec") or {}
    pct = report.get("stages_percent") or {}
    for k, v in sorted(stages.items(), key=lambda kv: -float(kv[1] or 0)):
        if float(v or 0) > 0:
            print(f"  {k:20} {v:8.2f}s  {pct.get(k, 0):5.1f}%")
    llm = report.get("llm") or {}
    print(f"\nLLM calls:   {llm.get('calls')}  avg_ms={llm.get('avg_ms')}")
    skips = llm.get("skip_reasons") or {}
    if skips:
        print(f"LLM skips:   {skips}")
    print(f"\nArtifacts:")
    for label, path in paths.items():
        print(f"  {label}: {path}")


def main() -> int:
    args = _parse_args()
    import os

    if args.profile:
        os.environ["VM_PERF_PROFILE"] = "1"
    if args.debug_artifacts:
        os.environ["VM_PERF_DEBUG"] = "1"

    from engines.benchmark_video import ensure_benchmark_video

    video = Path(args.video) if args.video else ensure_benchmark_video(APP_DIR)
    if not video or not Path(video).is_file():
        print(
            "ERROR: benchmark_video.mp4 not found.\n"
            "Place clip at data/stress_tests/benchmark_video.mp4 or run scripts/e2e_test.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"Benchmark video: {video}")
    print("Golden rule: analyze artifacts before changing architecture/models/prompts.\n")

    from app import app

    client = app.test_client()
    task_id = _run_dub(client, Path(video), args.target_lang, args.source_lang)
    print(f"Started task: {task_id}")

    profiler = cProfile.Profile() if (
        args.profile or os.getenv("VM_PERF_PROFILE", "").strip().lower() in ("1", "true", "yes")
    ) else None

    if profiler:
        profiler.enable()
    try:
        result = _wait_done(client, task_id, args.timeout)
    finally:
        if profiler:
            profiler.disable()
            prof_path = APP_DIR / "output" / "dev" / f"cprofile_{task_id}.prof"
            prof_path.parent.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(prof_path))
            print(f"\ncProfile saved: {prof_path}")
            stats = pstats.Stats(profiler)
            stats.sort_stats("cumulative")
            print("\nTop 15 functions by cumulative time:")
            stats.print_stats(15)

    status = result.get("status")
    info = result.get("info") or {}
    paths = {
        "performance_report_json": info.get("performance_report_json")
        or str(APP_DIR / "output" / "diagnostics" / task_id / "performance_report.json"),
        "timeline_json": info.get("timeline_json")
        or str(APP_DIR / "output" / "diagnostics" / task_id / "timeline.json"),
        "performance_log": info.get("performance_report"),
        "pipeline_timing_json": info.get("pipeline_timing_json"),
    }

    if status != "done":
        print(f"Run finished with status={status}", file=sys.stderr)
        errs = result.get("errors") or info.get("errors") or []
        if errs:
            print("Errors:", errs[:5], file=sys.stderr)

    _print_summary(task_id, paths)
    bn_path = Path(paths["performance_report_json"])
    if bn_path.is_file():
        bn = json.loads(bn_path.read_text(encoding="utf-8")).get("bottleneck") or {}
        if bn.get("exceeds_threshold"):
            print(
                f"\nNext step (TZ §3): profile stage '{bn.get('stage')}' with "
                "VM_PERF_PROFILE=1 / py-spy before changing code."
            )
    return 0 if status == "done" else 2


if __name__ == "__main__":
    raise SystemExit(main())
