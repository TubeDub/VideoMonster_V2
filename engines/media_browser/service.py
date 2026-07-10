"""Media Browser backend (TZ Etap 4)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.live.pipeline import LiveTranslationPipeline
from engines.platform_diagnostics.sink import PlatformTraceSink


@dataclass
class BrowserSession:
    session_id: str
    url: str
    tgt_lang: str
    live_session_id: str = ""
    status: str = "idle"
    sink: PlatformTraceSink | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class MediaBrowserService:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self._history_path = self.app_dir / "data" / "media_browser_history.json"
        self._sessions: dict[str, BrowserSession] = {}

    def _load_history(self) -> list[dict[str, Any]]:
        if not self._history_path.is_file():
            return []
        try:
            return json.loads(self._history_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_history(self, url: str, tgt_lang: str) -> None:
        hist = self._load_history()
        entry = {
            "url": url,
            "tgt_lang": tgt_lang,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        hist = [entry] + [h for h in hist if h.get("url") != url][:49]
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_path.write_text(
            json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def history(self) -> list[dict[str, Any]]:
        return self._load_history()

    def open(self, url: str, *, tgt_lang: str = "ru") -> dict[str, Any]:
        sid = uuid.uuid4().hex[:12]
        session = BrowserSession(
            session_id=sid,
            url=url.strip(),
            tgt_lang=(tgt_lang or "ru").split("-")[0].lower(),
            sink=PlatformTraceSink(self.app_dir, module="media_browser", session_id=sid),
        )
        self._sessions[sid] = session
        self._save_history(url, tgt_lang)
        session.sink.log(stage="media_browser.open", input_preview=url)
        session.status = "opened"
        return {"ok": True, "session_id": sid, "url": url}

    def start_translation(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session:
            return {"ok": False, "error": "Session not found"}
        pipeline = LiveTranslationPipeline(self.app_dir)
        session.live_session_id = pipeline.start(
            session.url, tgt_lang=session.tgt_lang
        )
        session.status = "translating"
        session.sink.log(
            stage="media_browser.translate_start",
            output_preview=session.live_session_id,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "live_session_id": session.live_session_id,
        }

    def get_live_events_url(self, session_id: str) -> str:
        session = self._sessions.get(session_id)
        if not session or not session.live_session_id:
            return ""
        return f"/api/platform/live/stream/{session.live_session_id}"
