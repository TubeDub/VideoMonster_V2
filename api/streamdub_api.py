"""StreamDub Engine REST API — separate from TubeDub auto_dub."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger("tubedub.api.streamdub")

bp = Blueprint("streamdub_api", __name__)

APP_DIR = Path(__file__).resolve().parent.parent
_DEBUG_LOG_PATH = APP_DIR / "debug-7e57dc.log"


def _debug_meaning_fit_route() -> None:
    """Temporary route diagnostic for debug session 7e57dc."""
    try:
        payload = {
            "sessionId": "7e57dc",
            "runId": "meaning-fit-route-discovery",
            "hypothesisId": "H7,H9",
            "location": "streamdub_api.py:api_streamdub_run",
            "message": "StreamDub run endpoint entered",
            "data": {
                "route": "/api/streamdub/run",
                "processCwdMatchesApp": Path.cwd().resolve() == APP_DIR.resolve(),
            },
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@bp.get("/api/streamdub/health")
def api_streamdub_health():
    from engines.streamdub import engine_info

    return jsonify({"ok": True, **engine_info(APP_DIR)})


@bp.get("/api/streamdub/modes")
def api_streamdub_modes():
    from engines.streamdub.pipeline.modes import MODE_STAGES
    from engines.streamdub.types import StreamDubMode

    return jsonify(
        {
            "modes": {
                m.value: {"stages": MODE_STAGES[m]}
                for m in StreamDubMode
            }
        }
    )


@bp.post("/api/streamdub/run")
def api_streamdub_run():
    """Run StreamDub pipeline on a video file."""
    # region agent log
    _debug_meaning_fit_route()
    # endregion
    data = request.get_json(silent=True) or {}
    video_path = (data.get("video_path") or "").strip()
    if not video_path:
        return jsonify({"error": "video_path required"}), 400

    from engines.streamdub import parse_mode, run_streamdub
    from engines.streamdub.types import StreamDubRequest

    req = StreamDubRequest(
        project_id=str(data.get("project_id") or uuid.uuid4().hex),
        video_path=video_path,
        audio_path=(data.get("audio_path") or "").strip() or None,
        source_lang=str(data.get("source_lang") or "en"),
        target_lang=str(data.get("target_lang") or "uk"),
        mode=parse_mode(data.get("mode")),
        voice=str(data.get("voice") or "uk-UA-OstapNeural"),
        model_size=str(data.get("model_size") or "tiny"),
        mt_backend=str(data.get("mt_backend") or "marian"),
    )

    try:
        result = _run_async(run_streamdub(req, app_dir=APP_DIR))
    except Exception as exc:
        logger.exception("StreamDub run failed")
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "ok": result.success,
            "project_id": result.project_id,
            "mode": result.mode.value,
            "segments": len(result.segments),
            "detected_lang": result.detected_lang,
            "output_audio": result.output_audio,
            "output_video": result.output_video,
            "stats": result.stats,
            "artifacts": result.artifacts,
            "errors": result.errors,
        }
    )
