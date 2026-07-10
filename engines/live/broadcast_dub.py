"""AI Live Dub — broadcast translation track (TZ Etap 3)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.live.pipeline import LiveTranslationPipeline
from engines.platform_diagnostics.sink import PlatformTraceSink


@dataclass
class BroadcastDubSession:
    session_id: str
    app_dir: Path
    audio_source: str
    tgt_lang: str
    src_lang: str
    rtmp_url: str = ""
    status: str = "idle"
    live_session_id: str = ""
    sink: PlatformTraceSink | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        app_dir: Path,
        *,
        audio_source: str,
        tgt_lang: str,
        src_lang: str = "auto",
        rtmp_url: str = "",
    ) -> "BroadcastDubSession":
        sid = uuid.uuid4().hex[:12]
        return cls(
            session_id=sid,
            app_dir=Path(app_dir),
            audio_source=audio_source.strip(),
            tgt_lang=(tgt_lang or "ru").split("-")[0].lower(),
            src_lang=(src_lang or "auto").split("-")[0].lower(),
            rtmp_url=rtmp_url.strip(),
            sink=PlatformTraceSink(Path(app_dir), module="broadcast_dub", session_id=sid),
        )

    def start(self) -> dict[str, Any]:
        """Bridge live translation pipeline for broadcast audio source."""
        self.sink.log(
            stage="broadcast_dub.start",
            input_preview=self.audio_source,
            meta={"tgt_lang": self.tgt_lang, "rtmp": self.rtmp_url},
        )
        pipeline = LiveTranslationPipeline(self.app_dir)
        self.live_session_id = pipeline.start(
            self.audio_source,
            tgt_lang=self.tgt_lang,
            src_lang=None if self.src_lang == "auto" else self.src_lang,
        )
        self.status = "running"
        self.meta["live_session_id"] = self.live_session_id
        return {
            "ok": True,
            "session_id": self.session_id,
            "live_session_id": self.live_session_id,
        }

    def stop(self) -> dict[str, Any]:
        if self.live_session_id:
            LiveTranslationPipeline(self.app_dir).stop(self.live_session_id)
        self.status = "stopped"
        self.sink.log(stage="broadcast_dub.stop")
        return {"ok": True, "session_id": self.session_id}

    def diagnostics(self) -> dict[str, Any]:
        snap = self.sink.snapshot()
        snap["status"] = self.status
        snap["live_session_id"] = self.live_session_id
        if self.live_session_id:
            snap["live"] = LiveTranslationPipeline(self.app_dir).diagnostics(
                self.live_session_id
            )
        return snap
