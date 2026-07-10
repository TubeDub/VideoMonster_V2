"""User recording API stub — punch in/out (TZ §12, YELLOW)."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from engines.core.feature_flags import is_developer

APP_DIR = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = APP_DIR / "output" / "recordings"
bp = Blueprint("recording_api", __name__)

_SESSIONS: dict[str, dict] = {}


def _dev_guard():
    if not is_developer(
        request_headers=dict(request.headers),
        request_cookies=dict(request.cookies),
    ):
        return jsonify({"ok": False, "error": "Developer mode required (YELLOW stub)"}), 403
    return None


@bp.post("/api/recording/punch-in")
def api_recording_punch_in():
    blocked = _dev_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or uuid.uuid4().hex[:12])
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = RECORDINGS_DIR / f"{session_id}_punch.wav"
    _SESSIONS[session_id] = {
        "session_id": session_id,
        "status": "recording",
        "path": str(path),
        "started_ms": int(time.time() * 1000),
        "segment_index": data.get("segment_index"),
    }
    return jsonify(
        {
            "ok": True,
            "stub": True,
            "status": "YELLOW",
            "session": _SESSIONS[session_id],
            "message": "Punch-in stub — audio capture not wired",
        }
    )


@bp.post("/api/recording/punch-out")
def api_recording_punch_out():
    blocked = _dev_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "")
    sess = _SESSIONS.get(session_id)
    if not sess:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    sess["status"] = "stopped"
    sess["ended_ms"] = int(time.time() * 1000)
    return jsonify(
        {
            "ok": True,
            "stub": True,
            "status": "YELLOW",
            "session": sess,
            "message": "Punch-out stub — no audio written",
        }
    )


@bp.get("/api/recording/status")
def api_recording_status():
    blocked = _dev_guard()
    if blocked:
        return blocked
    return jsonify({"ok": True, "sessions": list(_SESSIONS.values()), "readiness": "YELLOW"})
