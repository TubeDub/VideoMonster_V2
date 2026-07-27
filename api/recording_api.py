"""User recording API — punch in/out with file capture + FX (TZ §12)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from engines.core.feature_flags import is_developer, is_enabled

APP_DIR = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = APP_DIR / "output" / "recordings"
SESSIONS_DIR = RECORDINGS_DIR / "sessions"
bp = Blueprint("recording_api", __name__)

_LOCK = threading.RLock()
_SESSIONS: dict[str, dict] = {}


def _allowed() -> bool:
    if is_developer(
        request_headers=dict(request.headers),
        request_cookies=dict(request.cookies),
    ):
        return True
    try:
        return bool(
            is_enabled(
                "user_recording",
                developer_session=False,
                show_beta=True,
            )
        )
    except Exception:
        return False


def _guard():
    if not _allowed():
        return jsonify({"ok": False, "error": "Developer mode or FEATURE_USER_RECORDING required"}), 403
    return None


def _session_path(session_id: str) -> Path:
    safe = Path(str(session_id or "")).name
    if not safe or safe != str(session_id):
        raise ValueError("invalid_session_id")
    return SESSIONS_DIR / f"{safe}.json"


def _persist(session: dict) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path = _session_path(str(session["session_id"]))
    except ValueError:
        logger = __import__("logging").getLogger("tubedub.recording")
        logger.warning("refuse to persist invalid session_id")
        return
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_session(session_id: str) -> dict | None:
    safe = Path(str(session_id or "")).name
    if not safe or safe != str(session_id):
        return None
    with _LOCK:
        if safe in _SESSIONS:
            return _SESSIONS[safe]
    try:
        path = _session_path(safe)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        with _LOCK:
            _SESSIONS[safe] = data
        return data
    return None


def _studio():
    from engines.recording_studio.session import RecordingStudioSession

    return RecordingStudioSession.create(APP_DIR)


@bp.get("/api/recording/health")
def api_recording_health():
    return jsonify(
        {
            "ok": True,
            "module": "user_recording",
            "readiness": "GREEN",
            "allowed": _allowed(),
            "recordings_dir": str(RECORDINGS_DIR),
        }
    )


@bp.post("/api/recording/punch-in")
def api_recording_punch_in():
    blocked = _guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    raw_sid = str(data.get("session_id") or uuid.uuid4().hex[:12])
    session_id = Path(raw_sid).name
    if not session_id or session_id != raw_sid:
        return jsonify({"ok": False, "error": "invalid_session_id"}), 400
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = RECORDINGS_DIR / f"{session_id}_punch.wav"
    studio = _studio()
    sess = {
        "session_id": session_id,
        "status": "recording",
        "path": str(path),
        "started_ms": int(time.time() * 1000),
        "segment_index": data.get("segment_index"),
        "project_uuid": data.get("project_uuid"),
        "task_id": data.get("task_id"),
        "studio_session_id": studio.session_id,
        "studio_dir": str(studio.output_dir),
        "has_audio": False,
        "fx_applied": False,
    }
    with _LOCK:
        _SESSIONS[session_id] = sess
    _persist(sess)
    return jsonify(
        {
            "ok": True,
            "status": "recording",
            "readiness": "GREEN",
            "session": sess,
            "message": "Punch-in ready — upload audio on punch-out or /upload",
        }
    )


@bp.post("/api/recording/upload")
def api_recording_upload():
    """Upload recorded audio into an open punch session."""
    blocked = _guard()
    if blocked:
        return blocked
    session_id = str(request.form.get("session_id") or request.args.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"ok": False, "error": "session_id required"}), 400
    sess = _load_session(session_id)
    if not sess:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "file required"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "empty filename"}), 400

    sess = _save_upload_to_session(sess, f)
    with _LOCK:
        _SESSIONS[session_id] = sess
    _persist(sess)
    return jsonify({"ok": True, "session": sess})


def _save_upload_to_session(sess: dict, file_storage) -> dict:
    """Persist uploaded audio onto an existing session dict."""
    session_id = str(sess["session_id"])
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(file_storage.filename or "punch.wav").suffix.lower() or ".wav"
    dest = Path(sess.get("path") or (RECORDINGS_DIR / f"{session_id}_punch{ext}"))
    if dest.suffix.lower() != ext:
        dest = dest.with_suffix(ext)
    file_storage.save(str(dest))
    sess["path"] = str(dest)
    sess["has_audio"] = dest.is_file() and dest.stat().st_size > 0
    sess["uploaded_ms"] = int(time.time() * 1000)
    sess["original_name"] = file_storage.filename
    try:
        from engines.recording_studio.session import RecordingStudioSession

        studio = RecordingStudioSession(
            session_id=str(sess.get("studio_session_id") or session_id),
            app_dir=APP_DIR,
            output_dir=Path(str(sess.get("studio_dir") or (RECORDINGS_DIR / "studio" / session_id))),
        )
        studio.output_dir.mkdir(parents=True, exist_ok=True)
        sess["studio_track"] = studio.import_track(str(dest), name=session_id)
    except Exception as exc:  # noqa: BLE001
        sess["studio_error"] = str(exc)
    return sess


@bp.post("/api/recording/punch-out")
def api_recording_punch_out():
    blocked = _guard()
    if blocked:
        return blocked

    apply_fx = True
    # Support multipart (audio + session_id) or JSON
    if request.content_type and "multipart/form-data" in (request.content_type or ""):
        data = request.form.to_dict()
        session_id = str(data.get("session_id") or "")
        apply_fx = str(data.get("apply_fx", "1")).lower() not in ("0", "false", "no")
        sess = _load_session(session_id)
        if not sess:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if "file" in request.files and request.files["file"].filename:
            sess = _save_upload_to_session(sess, request.files["file"])
    else:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get("session_id") or "")
        sess = _load_session(session_id)
        if not sess:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if "apply_fx" in data:
            apply_fx = bool(data.get("apply_fx"))

    audio_path = Path(str(sess.get("path") or ""))
    processed = None
    if apply_fx and audio_path.is_file() and audio_path.stat().st_size > 0:
        try:
            from engines.recording_studio.session import FxPreset, RecordingStudioSession

            studio = RecordingStudioSession(
                session_id=str(sess.get("studio_session_id") or session_id),
                app_dir=APP_DIR,
                output_dir=Path(str(sess.get("studio_dir") or (RECORDINGS_DIR / "studio" / session_id))),
            )
            studio.output_dir.mkdir(parents=True, exist_ok=True)
            fx = studio.apply_fx(str(audio_path), FxPreset())
            if fx.get("ok"):
                processed = fx.get("output")
                sess["fx"] = fx
                sess["fx_applied"] = True
                sess["processed_path"] = processed
        except Exception as exc:  # noqa: BLE001
            sess["fx_error"] = str(exc)

    sess["status"] = "stopped"
    sess["ended_ms"] = int(time.time() * 1000)
    sess["duration_ms"] = int(sess["ended_ms"]) - int(sess.get("started_ms") or sess["ended_ms"])
    sess["has_audio"] = audio_path.is_file() and audio_path.stat().st_size > 0
    with _LOCK:
        _SESSIONS[session_id] = sess
    _persist(sess)

    return jsonify(
        {
            "ok": True,
            "status": "stopped",
            "readiness": "GREEN" if sess.get("has_audio") else "YELLOW",
            "session": sess,
            "audio_path": sess.get("path"),
            "processed_path": processed,
            "message": "Punch-out complete" if sess.get("has_audio") else "Punch-out — no audio uploaded",
        }
    )


@bp.post("/api/recording/<session_id>/fx")
def api_recording_fx(session_id: str):
    blocked = _guard()
    if blocked:
        return blocked
    safe = Path(session_id).name
    sess = _load_session(safe)
    if not sess:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    audio_path = Path(str(sess.get("path") or ""))
    if not audio_path.is_file():
        return jsonify({"ok": False, "error": "No audio on session"}), 400
    body = request.get_json(silent=True) or {}
    from engines.recording_studio.session import FxPreset, RecordingStudioSession

    preset = FxPreset(
        noise_reduction=bool(body.get("noise_reduction", True)),
        compress=bool(body.get("compress", True)),
        limit=bool(body.get("limit", True)),
        normalize=bool(body.get("normalize", True)),
        eq_highpass=bool(body.get("eq_highpass", True)),
    )
    studio = RecordingStudioSession(
        session_id=str(sess.get("studio_session_id") or safe),
        app_dir=APP_DIR,
        output_dir=Path(str(sess.get("studio_dir") or (RECORDINGS_DIR / "studio" / safe))),
    )
    studio.output_dir.mkdir(parents=True, exist_ok=True)
    result = studio.apply_fx(str(audio_path), preset)
    if result.get("ok"):
        sess["processed_path"] = result.get("output")
        sess["fx"] = result
        sess["fx_applied"] = True
        _persist(sess)
    return jsonify({"ok": bool(result.get("ok")), "session": sess, "fx": result})


@bp.get("/api/recording/<session_id>/audio")
def api_recording_audio(session_id: str):
    blocked = _guard()
    if blocked:
        return blocked
    safe = Path(session_id).name
    sess = _load_session(safe)
    if not sess:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    prefer = request.args.get("processed")
    path = Path(str(sess.get("processed_path") if prefer else sess.get("path") or ""))
    if prefer and not path.is_file():
        path = Path(str(sess.get("path") or ""))
    if not path.is_file():
        return jsonify({"ok": False, "error": "Audio not found"}), 404
    return send_file(path, as_attachment=False, download_name=path.name)


@bp.get("/api/recording/status")
def api_recording_status():
    blocked = _guard()
    if blocked:
        return blocked
    # Merge disk sessions
    if SESSIONS_DIR.is_dir():
        for path in SESSIONS_DIR.glob("*.json"):
            sid = path.stem
            if sid not in _SESSIONS:
                _load_session(sid)
    with _LOCK:
        sessions = list(_SESSIONS.values())
    ready = sum(1 for s in sessions if s.get("has_audio"))
    return jsonify(
        {
            "ok": True,
            "sessions": sessions,
            "count": len(sessions),
            "with_audio": ready,
            "readiness": "GREEN",
        }
    )


@bp.get("/api/recording/<session_id>")
def api_recording_session(session_id: str):
    blocked = _guard()
    if blocked:
        return blocked
    safe = Path(session_id).name
    sess = _load_session(safe)
    if not sess:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    return jsonify({"ok": True, "session": sess})
