"""TubeDub AI Media Platform — REST + SSE API."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("platform_api", __name__)

_STREAMING: dict[str, Any] = {}
_BROADCAST: dict[str, Any] = {}
_RECORDING: dict[str, Any] = {}
_LOCK = threading.Lock()


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def _guard(module: str):
    from engines.platform.config import require_module

    try:
        require_module(module)
    except PermissionError as e:
        return str(e)
    return None


@bp.get("/api/platform/status")
def api_platform_status():
    from engines.platform.config import platform_status

    return jsonify(platform_status())


# ── Etap 1: Live Translation ─────────────────────────────


@bp.post("/api/platform/live/start")
def api_live_start():
    blocked = _guard("live")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    uri = (data.get("url") or data.get("path") or data.get("source") or "").strip()
    if not uri:
        return _err("url or path required")
    # Local filesystem sources must stay under uploads/output (no arbitrary path read).
    if not uri.lower().startswith(("http://", "https://", "rtmp://", "rtmps://", "srt://")):
        from engines.path_safety import resolve_under_roots

        hit = resolve_under_roots(
            uri,
            [APP_DIR / "uploads", APP_DIR / "output", APP_DIR / "projects"],
            basename_fallback=True,
        )
        if hit is None:
            return _err("local_source_outside_allowlist", 400)
        uri = str(hit)
    from engines.live.pipeline import LiveTranslationPipeline
    from engines.live.preflight import preflight_live

    pf = preflight_live(require_stt=True)
    if not pf.get("ok") and data.get("require_engines", True):
        return jsonify(
            {
                "ok": False,
                "error": "; ".join(pf.get("issues") or ["live preflight failed"]),
                "preflight": pf,
            }
        ), 503
    try:
        sid = LiveTranslationPipeline(APP_DIR).start(
            uri,
            tgt_lang=data.get("tgt_lang") or "ru",
            src_lang=data.get("src_lang"),
            voice=data.get("voice") or "",
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:400]}), 500
    return jsonify({"ok": True, "session_id": sid, "preflight": pf})


@bp.get("/api/platform/live/preflight")
def api_live_preflight():
    blocked = _guard("live")
    if blocked:
        return _err(blocked, 403)
    from engines.live.preflight import preflight_live

    return jsonify({"ok": True, **preflight_live()})

def _safe_session_id(session_id: str) -> str | None:
    safe = Path(str(session_id or "")).name
    if not safe or safe != str(session_id):
        return None
    return safe


@bp.post("/api/platform/live/stop/<session_id>")
def api_live_stop(session_id: str):
    blocked = _guard("live")
    if blocked:
        return _err(blocked, 403)
    safe = _safe_session_id(session_id)
    if not safe:
        return _err("invalid_session_id", 400)
    from engines.live.pipeline import LiveTranslationPipeline

    LiveTranslationPipeline(APP_DIR).stop(safe)
    return jsonify({"ok": True})


@bp.get("/api/platform/live/stream/<session_id>")
def api_live_stream(session_id: str):
    blocked = _guard("live")
    if blocked:
        return _err(blocked, 403)
    safe = _safe_session_id(session_id)
    if not safe:
        return _err("invalid_session_id", 400)
    from engines.live.pipeline import LiveTranslationPipeline

    after = int(request.args.get("after") or 0)

    def generate():
        pipe = LiveTranslationPipeline(APP_DIR)
        for ev in pipe.subscribe_events(safe, after=after, timeout_sec=300.0):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@bp.get("/api/platform/live/diagnostics/<session_id>")
def api_live_diagnostics(session_id: str):
    safe = _safe_session_id(session_id)
    if not safe:
        return _err("invalid_session_id", 400)
    from engines.live.pipeline import LiveTranslationPipeline

    try:
        return jsonify(LiveTranslationPipeline(APP_DIR).diagnostics(safe))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:400], "session_id": safe}), 500


# ── Etap 2: Streaming Studio ─────────────────────────────


@bp.get("/api/platform/streaming/capabilities")
def api_streaming_capabilities():
    blocked = _guard("streaming")
    if blocked:
        return _err(blocked, 403)
    from engines.streaming_studio.session import probe_streaming_capabilities

    return jsonify({"ok": True, **probe_streaming_capabilities()})


@bp.post("/api/platform/streaming/session/start")
def api_streaming_start():
    blocked = _guard("streaming")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    from engines.streaming_studio.session import CaptureSpec, StreamingSession

    spec = CaptureSpec(
        screen=bool(data.get("screen")),
        webcam=bool(data.get("webcam")),
        microphone=bool(data.get("microphone", True)),
        system_audio=bool(data.get("system_audio")),
        rtmp_url=str(data.get("rtmp_url") or ""),
        input_file=str(data.get("input_file") or data.get("path") or ""),
    )
    session = StreamingSession.create(APP_DIR, spec)
    with _LOCK:
        _STREAMING[session.session_id] = session
    result = session.start_record()
    result["session_id"] = session.session_id
    return jsonify(result)


@bp.post("/api/platform/streaming/session/stop/<session_id>")
def api_streaming_stop(session_id: str):
    blocked = _guard("streaming")
    if blocked:
        return _err(blocked, 403)
    with _LOCK:
        session = _STREAMING.get(session_id)
    if not session:
        return _err("Session not found", 404)
    return jsonify(session.stop_all() if hasattr(session, "stop_all") else session.stop_record())


@bp.post("/api/platform/streaming/rtmp/<session_id>")
def api_streaming_rtmp(session_id: str):
    blocked = _guard("streaming")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    with _LOCK:
        session = _STREAMING.get(session_id)
    if not session:
        return _err("Session not found", 404)
    return jsonify(session.start_rtmp(data.get("rtmp_url")))


@bp.post("/api/platform/streaming/file-to-rtmp")
def api_streaming_file_to_rtmp():
    """Record→stream shortcut: local media file → RTMP via FFmpeg."""
    blocked = _guard("streaming")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or data.get("input_file") or "").strip()
    if not path:
        return _err("path required")
    from engines.streaming_studio.session import CaptureSpec, StreamingSession

    spec = CaptureSpec(
        microphone=False,
        rtmp_url=str(data.get("rtmp_url") or ""),
        input_file=path,
    )
    session = StreamingSession.create(APP_DIR, spec)
    with _LOCK:
        _STREAMING[session.session_id] = session
    result = session.file_to_rtmp(path, data.get("rtmp_url"))
    result["session_id"] = session.session_id
    return jsonify(result)

# ── Etap 3: AI Live Dub ──────────────────────────────────


@bp.post("/api/platform/broadcast-dub/start")
def api_broadcast_dub_start():
    blocked = _guard("broadcast_dub")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    source = (data.get("audio_source") or data.get("path") or "").strip()
    if not source:
        return _err("audio_source required")
    from engines.live.broadcast_dub import BroadcastDubSession

    session = BroadcastDubSession.create(
        APP_DIR,
        audio_source=source,
        tgt_lang=data.get("tgt_lang") or "ru",
        src_lang=data.get("src_lang") or "auto",
        rtmp_url=str(data.get("rtmp_url") or ""),
    )
    with _LOCK:
        _BROADCAST[session.session_id] = session
    result = session.start()
    return jsonify(result)


@bp.post("/api/platform/broadcast-dub/stop/<session_id>")
def api_broadcast_dub_stop(session_id: str):
    blocked = _guard("broadcast_dub")
    if blocked:
        return _err(blocked, 403)
    with _LOCK:
        session = _BROADCAST.get(session_id)
    if not session:
        return _err("Not found", 404)
    return jsonify(session.stop())


# ── Etap 4: Media Browser ────────────────────────────────


@bp.get("/api/platform/browser/history")
def api_browser_history():
    blocked = _guard("media_browser")
    if blocked:
        return _err(blocked, 403)
    from engines.media_browser.service import MediaBrowserService

    return jsonify({"ok": True, "history": MediaBrowserService(APP_DIR).history()})


@bp.post("/api/platform/browser/open")
def api_browser_open():
    blocked = _guard("media_browser")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return _err("url required")
    from engines.media_browser.service import MediaBrowserService

    svc = MediaBrowserService(APP_DIR)
    return jsonify(svc.open(url, tgt_lang=data.get("tgt_lang") or "ru"))


@bp.post("/api/platform/browser/translate/<session_id>")
def api_browser_translate(session_id: str):
    blocked = _guard("media_browser")
    if blocked:
        return _err(blocked, 403)
    from engines.media_browser.service import MediaBrowserService

    svc = MediaBrowserService(APP_DIR)
    result = svc.start_translation(session_id)
    if result.get("ok") and result.get("live_session_id"):
        result["events_url"] = f"/api/platform/live/stream/{result['live_session_id']}"
    return jsonify(result)


# ── Etap 5: Recording Studio ─────────────────────────────


@bp.post("/api/platform/recording/session")
def api_recording_session():
    blocked = _guard("recording")
    if blocked:
        return _err(blocked, 403)
    from engines.recording_studio.session import RecordingStudioSession

    session = RecordingStudioSession.create(APP_DIR)
    with _LOCK:
        _RECORDING[session.session_id] = session
    return jsonify({"ok": True, "session_id": session.session_id})


@bp.post("/api/platform/recording/import/<session_id>")
def api_recording_import(session_id: str):
    blocked = _guard("recording")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    with _LOCK:
        session = _RECORDING.get(session_id)
    if not session:
        return _err("Session not found", 404)
    return jsonify(session.import_track(path, name=data.get("name") or ""))


@bp.post("/api/platform/recording/fx/<session_id>")
def api_recording_fx(session_id: str):
    blocked = _guard("recording")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    track = (data.get("track") or "").strip()
    with _LOCK:
        session = _RECORDING.get(session_id)
    if not session:
        return _err("Session not found", 404)
    return jsonify(session.apply_fx(track))


# ── Etap 6–7: Voice / Vocal Training ─────────────────────


@bp.post("/api/platform/voice-training/analyze")
def api_voice_training():
    blocked = _guard("voice_training")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    path = (data.get("wav_path") or data.get("path") or "").strip()
    if not path:
        return _err("wav_path required")
    from engines.voice_training.analyzer import analyze_voice_recording

    result = analyze_voice_recording(
        path,
        script=data.get("script") or "",
        app_dir=APP_DIR,
        session_id=uuid.uuid4().hex[:8],
    )
    return jsonify(
        {
            "ok": True,
            "metrics": result.metrics.__dict__,
            "recommendations": result.recommendations,
            "transcript": result.transcript,
        }
    )


@bp.post("/api/platform/vocal-training/analyze")
def api_vocal_training():
    blocked = _guard("vocal_training")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    path = (data.get("wav_path") or data.get("path") or "").strip()
    if not path:
        return _err("wav_path required")
    from engines.vocal_training.analyzer import analyze_vocal_recording

    target = data.get("target_note_hz")
    result = analyze_vocal_recording(
        path,
        target_note_hz=float(target) if target else None,
        app_dir=APP_DIR,
    )
    return jsonify(
        {
            "ok": True,
            "score": result.score,
            "metrics": result.metrics.__dict__,
            "recommendations": result.recommendations,
        }
    )


# ── Etap 8–9: Diagnostics + Assistant ──────────────────


@bp.get("/api/platform/dev/trace/<module>/<session_id>")
def api_dev_trace(module: str, session_id: str):
    path = APP_DIR / "output" / "dev" / module / f"{module}_{session_id}.json"
    if not path.is_file():
        return _err("Trace not found", 404)
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


@bp.get("/api/platform/assistant/analyze/<module>/<session_id>")
def api_assistant_analyze(module: str, session_id: str):
    blocked = _guard("assistant")
    if blocked:
        return _err(blocked, 403)
    from engines.ai_assistant.analyzer import analyze_session_dir

    return jsonify(analyze_session_dir(APP_DIR, module, session_id))


@bp.post("/api/platform/assistant/review")
def api_assistant_review():
    blocked = _guard("assistant")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    from engines.ai_assistant.analyzer import analyze_translation_review_segment

    issues = analyze_translation_review_segment(
        source=data.get("source") or "",
        translated=data.get("translated") or "",
        router_reason=data.get("router_reason") or "",
    )
    return jsonify({"ok": True, "issues": issues})


# ── Realtime Interpreter + Screen Dub (MVP on live pipeline) ──


@bp.post("/api/platform/interpreter/start")
def api_interpreter_start():
    blocked = _guard("live")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    uri = (data.get("url") or data.get("path") or data.get("source") or "").strip()
    if not uri:
        return _err("url or path required")
    from engines.interpreter import start_realtime_interpreter

    sess = start_realtime_interpreter(
        APP_DIR,
        uri,
        src_lang=data.get("src_lang") or "auto",
        tgt_lang=data.get("tgt_lang") or "ru",
        voice=data.get("voice") or "",
    )
    return jsonify(
        {
            "ok": True,
            "session": sess.to_dict(),
            "events_url": f"/api/platform/live/stream/{sess.live_session_id}",
        }
    )


@bp.post("/api/platform/screen-dub/start")
def api_screen_dub_start():
    blocked = _guard("live")
    if blocked:
        return _err(blocked, 403)
    data = request.get_json(silent=True) or {}
    uri = (data.get("url") or data.get("path") or data.get("source") or "").strip()
    if not uri:
        return _err("url or path required")
    from engines.interpreter import start_screen_dub

    sess = start_screen_dub(
        APP_DIR,
        uri,
        src_lang=data.get("src_lang") or "auto",
        tgt_lang=data.get("tgt_lang") or "ru",
        voice=data.get("voice") or "",
    )
    return jsonify(
        {
            "ok": True,
            "session": sess.to_dict(),
            "events_url": f"/api/platform/live/stream/{sess.live_session_id}",
        }
    )


@bp.post("/api/platform/interpreter/stop/<session_id>")
def api_interpreter_stop(session_id: str):
    from engines.interpreter import stop_session

    ok = stop_session(APP_DIR, session_id)
    return jsonify({"ok": ok})


@bp.get("/api/platform/interpreter/sessions")
def api_interpreter_sessions():
    from engines.interpreter import list_sessions

    return jsonify({"ok": True, "sessions": list_sessions()})
