"""StreamDub Engine REST API — separate from TubeDub auto_dub."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger("tubedub.api.streamdub")

bp = Blueprint("streamdub_api", __name__)

APP_DIR = Path(__file__).resolve().parent.parent


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
    data = request.get_json(silent=True) or {}
    video_path = (data.get("video_path") or "").strip()
    if not video_path:
        return jsonify({"error": "video_path required"}), 400

    from engines.path_safety import resolve_under_roots
    from engines.streamdub import parse_mode, run_streamdub
    from engines.streamdub.types import StreamDubRequest

    resolved = resolve_under_roots(
        video_path,
        [APP_DIR / "uploads", APP_DIR / "uploads" / "imports", APP_DIR / "output"],
        basename_fallback=True,
    )
    if resolved is None:
        return jsonify({"error": "video not found under uploads/output"}), 404

    req = StreamDubRequest(
        project_id=str(data.get("project_id") or uuid.uuid4().hex),
        video_path=str(resolved),
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
