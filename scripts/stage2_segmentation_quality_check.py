# -*- coding: utf-8 -*-
"""Stage 2 quality check: Happy Path STT glue + Simple dub on a ~2 min clip.

Writes:
  output/stage2_segmentation_result.json
  (and final MP4 under output/ if pipeline completes)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

SRC_CANDIDATES = [
    APP_DIR / "uploads" / "video_076d1b49ad.mp4",
    APP_DIR / "uploads" / "video_0c3e6038c7.mp4",
]
CLIP = APP_DIR / "uploads" / "stage2_happy_path_clip.mp4"
RESULT = APP_DIR / "output" / "stage2_segmentation_result.json"
CLIP_SEC = 110


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def ensure_clip() -> Path | None:
    for src in SRC_CANDIDATES:
        if src.is_file() and src.stat().st_size > 500_000:
            break
    else:
        print("NO_SOURCE_VIDEO")
        return None
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        print("NO_FFMPEG")
        return None
    if CLIP.is_file() and CLIP.stat().st_size > 100_000:
        return CLIP
    CLIP.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-ss",
            "10",
            "-i",
            str(src),
            "-t",
            str(CLIP_SEC),
            "-c",
            "copy",
            str(CLIP),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not CLIP.is_file():
        print("CLIP_FAIL", (proc.stderr or "")[-400:])
        return None
    return CLIP


def synthetic_merge_probe() -> dict:
    from engines.segment_merger import merge_stt_segments_happy_path

    texts = [f"Line number {i} of the speech." for i in range(24)]
    timing = []
    t = 0
    for _ in range(24):
        timing.append({"start": t, "end": t + 1200})
        t += 1400  # 200ms gap
    merged, mt = merge_stt_segments_happy_path(texts, timing)
    durs = [r["end"] - r["start"] for r in mt]
    return {
        "segments_before": len(texts),
        "segments_after": len(merged),
        "min_dur_ms": min(durs) if durs else 0,
        "median_dur_ms": sorted(durs)[len(durs) // 2] if durs else 0,
        "ok_fewer": len(merged) < len(texts),
    }


def main() -> int:
    from app import app

    out: dict = {
        "synthetic": synthetic_merge_probe(),
        "pipeline": {},
    }
    clip = ensure_clip()
    if not clip:
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("WROTE", RESULT, "(no real clip)")
        return 1

    client = app.test_client()
    r = client.get("/api/license/status")
    lic = r.get_json() or {}
    if not lic.get("features", {}).get("auto_dub"):
        from engines.license_manager import activate_key, generate_key

        ok, _, msg = activate_key(generate_key("TEST-7"))
        print("license:", ok, msg)

    with open(clip, "rb") as f:
        r = client.post("/api/dub/upload_video", data={"file": (f, clip.name)})
    up = r.get_json() or {}
    print("upload", r.status_code, up)
    if r.status_code != 200:
        out["pipeline"]["error"] = "upload_failed"
        RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    r = client.post(
        "/api/auto_dub/start",
        json={
            "video_path": "uploads/" + up.get("filename", clip.name),
            "target_lang": "uk",
            "source_lang": "en",
            "voice": "uk-UA-OstapNeural",
            "model_size": "tiny",
            "dub_style": "modern",
            "user_mode": "basic",
            "ui_lang": "uk",
            "translation_review_before_tts": True,
        },
    )
    start = r.get_json() or {}
    print("start", r.status_code, start)
    out["pipeline"]["start"] = {"status": r.status_code, "body": start}
    if r.status_code == 409 and start.get("error_code") == "prepare_required":
        out["pipeline"]["skipped"] = "prepare_required"
        RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SKIP prepare_required")
        return 0
    if r.status_code != 200:
        RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    task_id = start["task_id"]
    out["pipeline"]["task_id"] = task_id
    deadline = time.time() + 1800  # 30 min
    last = {}
    for i in range(1800):
        if time.time() > deadline:
            out["pipeline"]["error"] = "timeout"
            break
        r = client.get(f"/api/auto_dub/status/{task_id}")
        st = r.get_json() or {}
        last = st
        if i % 15 == 0:
            print(
                f"  [{i}s] {st.get('status')} {st.get('step_label')} "
                f"{st.get('progress')}%"
            )
        if st.get("status") == "translation_review":
            ar = client.post(
                f"/api/auto_dub/translation_review/{task_id}/approve",
                json={},
            )
            print("  review approved", ar.status_code)
            continue
        if st.get("status") == "done":
            out["pipeline"]["status"] = "done"
            out["pipeline"]["output_file"] = st.get("output_file")
            break
        if st.get("status") == "error":
            out["pipeline"]["status"] = "error"
            out["pipeline"]["errors"] = st.get("errors") or st.get("error")
            break
        time.sleep(1)

    # Pull task info diagnostics
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id) or {}
            info = dict(task.get("info") or {})
        out["pipeline"]["adaptation_path"] = info.get("adaptation_path")
        out["pipeline"]["happy_path"] = info.get("happy_path")
        out["pipeline"]["segments_before"] = info.get("segments_before") or info.get(
            "stt_merge_before"
        )
        out["pipeline"]["segments_after"] = info.get("segments_after") or info.get(
            "stt_merge_after"
        )
        out["pipeline"]["mt_batch_mode"] = info.get("mt_batch_mode")
        out["pipeline"]["mt_batch_groups"] = info.get("mt_batch_groups")
        out["pipeline"]["mt_batch_segments"] = info.get("mt_batch_segments")
        out["pipeline"]["adaptive_segmentation_skipped"] = info.get(
            "adaptive_segmentation_skipped"
        )
        timing_rows = list(info.get("timing_fit_segments") or [])
        out["pipeline"]["timing_fit_segments"] = timing_rows[:40]
        atempos = [float(r.get("atempo") or 1.0) for r in timing_rows]
        overflows = [int(r.get("overflow_ms") or 0) for r in timing_rows]
        trimmed = [bool(r.get("speech_trimmed")) for r in timing_rows]
        out["pipeline"]["timing_summary"] = {
            "n": len(timing_rows),
            "max_atempo": max(atempos) if atempos else None,
            "atempo_over_1_20": any(a > 1.2001 for a in atempos),
            "overflow_count": sum(1 for o in overflows if o > 0),
            "speech_trimmed_count": sum(1 for t in trimmed if t),
        }
        out["pipeline"]["meaning_fit_skipped"] = info.get("meaning_fit_skipped")
        out["pipeline"]["timing_aware_skipped"] = info.get("timing_aware_skipped")
    except Exception as exc:
        out["pipeline"]["info_error"] = str(exc)

    out["pipeline"]["last_status"] = {
        "status": last.get("status"),
        "step": last.get("step_label"),
        "progress": last.get("progress"),
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", RESULT)
    print(json.dumps(out["pipeline"].get("timing_summary"), ensure_ascii=False))
    print(
        "segments",
        out["pipeline"].get("segments_before"),
        "->",
        out["pipeline"].get("segments_after"),
    )
    return 0 if out["pipeline"].get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
