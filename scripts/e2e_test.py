"""E2E smoke test for VideoMonster V2 pipeline."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from app import app  # noqa: E402

TEST_VIDEO = APP_DIR / "uploads" / "test_e2e_speech.mp4"
SPEECH_LINE = "Hello world, this is a VideoMonster end to end test."


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


async def _make_speech_mp3(path: Path) -> bool:
    try:
        import edge_tts

        communicate = edge_tts.Communicate(SPEECH_LINE, "en-US-JennyNeural")
        await communicate.save(str(path))
        return path.exists() and path.stat().st_size > 0
    except Exception as e:
        print("Speech generation failed:", e)
        return False


def ensure_test_video() -> bool:
    """Create a short MP4 with real English speech so Whisper returns text."""
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        print("SKIP pipeline: FFmpeg not in PATH. Install from https://ffmpeg.org")
        return False

    uploads = APP_DIR / "uploads"
    uploads.mkdir(exist_ok=True)

    if TEST_VIDEO.exists() and TEST_VIDEO.stat().st_size > 5000:
        return True

    speech_mp3 = uploads / "test_e2e_speech.mp3"
    if not speech_mp3.exists():
        ok = asyncio.run(_make_speech_mp3(speech_mp3))
        if not ok:
            print("SKIP pipeline: edge-tts unavailable (need internet + pip install edge-tts)")
            return False

    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=4:size=320x240:rate=25",
            "-i",
            str(speech_mp3),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(TEST_VIDEO),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print("SKIP pipeline: ffmpeg mux failed:", (proc.stderr or "")[-400:])
        return False

    ok = TEST_VIDEO.exists() and TEST_VIDEO.stat().st_size > 0
    if not ok:
        print("SKIP pipeline: could not create test video")
    return ok


def main() -> int:
    print("=== VideoMonster V2 E2E ===")
    client = app.test_client()

    r = client.get("/api/system/check")
    print("System check:", r.status_code, json.dumps(r.get_json(), ensure_ascii=False)[:500])

    r = client.get("/dub")
    print(
        "Dub page:",
        r.status_code,
        "OK" if r.status_code == 200 and b"btn-start-dub" in r.data else "FAIL",
    )

    r = client.get("/api/license/status")
    lic = r.get_json() or {}
    print("License tier:", lic.get("tier"), "auto_dub:", lic.get("features", {}).get("auto_dub"))

    if not lic.get("features", {}).get("auto_dub"):
        from engines.license_manager import activate_key, generate_key

        test_key = generate_key("TEST-7")
        ok, _, msg = activate_key(test_key)
        print("License activate for E2E:", ok, msg)
        if not ok:
            print("SKIP pipeline: auto_dub not licensed")
            return 0

    if not ensure_test_video():
        return 0

    with open(TEST_VIDEO, "rb") as f:
        r = client.post("/api/dub/upload_video", data={"file": (f, TEST_VIDEO.name)})
    up = r.get_json() or {}
    print("Upload:", r.status_code, up)
    if r.status_code != 200:
        return 1

    r = client.post(
        "/api/auto_dub/start",
        json={
            "video_path": "uploads/" + up.get("filename", TEST_VIDEO.name),
            "target_lang": "ru",
            "source_lang": "en",
            "voice": "ru-RU-DmitryNeural",
            "model_size": "tiny",
            "dub_mode": "replace",
            "mix_volume": 0.3,
            "ui_lang": "ru",
        },
    )
    start = r.get_json() or {}
    print("Start:", r.status_code, start)
    if r.status_code == 409 and start.get("error_code") == "prepare_required":
        # Language models not downloaded/prepared in this environment — this is
        # expected offline; treat as a graceful skip (like missing ffmpeg/tts).
        print("SKIP pipeline: models not prepared (prepare_required) — run prepare first")
        return 0
    if r.status_code != 200:
        return 1

    task_id = start["task_id"]
    for i in range(600):
        r = client.get(f"/api/auto_dub/status/{task_id}")
        st = r.get_json() or {}
        if i % 10 == 0:
            print(
                f"  [{i}s] {st.get('status')} {st.get('step_label')} {st.get('progress')}%"
            )
        if st.get("status") == "translation_review":
            ar = client.post(
                f"/api/auto_dub/translation_review/{task_id}/approve",
                json={},
            )
            print(
                f"  [{i}s] auto-approved translation review:",
                ar.status_code,
                ar.get_json(),
            )
            continue
        if st.get("status") == "done":
            out = st.get("output_file")
            out_path = APP_DIR / "output" / out if out else None
            ok = out_path and out_path.exists() and out_path.stat().st_size > 0
            print("DONE:", out, "size=", out_path.stat().st_size if ok else 0)
            perf_log = APP_DIR / "output" / "dev" / "performance_report.log"
            if perf_log.is_file():
                tail = perf_log.read_text(encoding="utf-8").split("===")[-1]
                print("\n--- performance_report (last entry) ---")
                print("===" + tail[-1200:])
            return 0 if ok else 1
        if st.get("status") == "error":
            print("ERROR:", st.get("errors"))
            return 1
        time.sleep(1)
    print("TIMEOUT")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
