"""Realtime interpreter + screen dub — MVP built on LiveTranslationPipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class InterpreterSession:
    session_id: str
    mode: str
    live_session_id: str
    status: str = "running"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "live_session_id": self.live_session_id,
            "status": self.status,
            "meta": dict(self.meta),
        }


_SESSIONS: dict[str, InterpreterSession] = {}


def start_realtime_interpreter(
    app_dir: Path,
    source_uri: str,
    *,
    src_lang: str = "auto",
    tgt_lang: str = "ru",
    voice: str = "",
) -> InterpreterSession:
    """Low-latency phrase interpreter — reuses live chunk pipeline."""
    from engines.live.pipeline import LiveTranslationPipeline

    live_id = LiveTranslationPipeline(app_dir).start(
        source_uri,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        voice=voice or "ru-RU-DmitryNeural",
    )
    sid = f"interp_{live_id}"
    sess = InterpreterSession(
        session_id=sid,
        mode="realtime_interpreter",
        live_session_id=live_id,
        meta={"source": source_uri, "tgt_lang": tgt_lang},
    )
    _SESSIONS[sid] = sess
    return sess


def start_screen_dub(
    app_dir: Path,
    source_uri: str,
    *,
    src_lang: str = "auto",
    tgt_lang: str = "ru",
    voice: str = "",
) -> InterpreterSession:
    """Screen / desktop capture dub — same live path with screen_dub tag."""
    from engines.live.pipeline import LiveTranslationPipeline

    live_id = LiveTranslationPipeline(app_dir).start(
        source_uri,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        voice=voice or "ru-RU-DmitryNeural",
    )
    sid = f"screen_{live_id}"
    sess = InterpreterSession(
        session_id=sid,
        mode="screen_dub",
        live_session_id=live_id,
        meta={"source": source_uri, "tgt_lang": tgt_lang, "capture": "screen_or_file"},
    )
    _SESSIONS[sid] = sess
    return sess


def stop_session(app_dir: Path, session_id: str) -> bool:
    sess = _SESSIONS.get(session_id)
    if not sess:
        return False
    from engines.live.pipeline import LiveTranslationPipeline

    LiveTranslationPipeline(app_dir).stop(sess.live_session_id)
    sess.status = "stopped"
    return True


def get_session(session_id: str) -> InterpreterSession | None:
    return _SESSIONS.get(session_id)


def list_sessions() -> list[dict[str, Any]]:
    return [s.to_dict() for s in _SESSIONS.values()]
