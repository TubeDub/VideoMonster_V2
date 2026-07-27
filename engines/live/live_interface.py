"""Live translation interface — adapter to engines.live.pipeline (YELLOW/GREEN)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class LiveSessionInfo:
    session_id: str
    status: str = "idle"
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "error": self.error,
            "meta": dict(self.meta),
        }


class LiveInterface(ABC):
    """Abstract live dub/translation session."""

    readiness: str = "RED"

    @abstractmethod
    def start(
        self,
        source_uri: str,
        *,
        src_lang: str,
        tgt_lang: str,
        voice: str = "",
    ) -> LiveSessionInfo:
        """Start live session."""

    @abstractmethod
    def stop(self, session_id: str) -> bool:
        """Stop live session."""

    @abstractmethod
    def events(self, session_id: str, after: int = 0) -> Iterator[dict[str, Any]]:
        """Stream session events."""


class LivePipelineAdapter(LiveInterface):
    """Production adapter — chunk STT → translate → TTS via LiveTranslationPipeline."""

    readiness = "GREEN"

    def __init__(self, app_dir: Path) -> None:
        self.app_dir = Path(app_dir)

    def start(
        self,
        source_uri: str,
        *,
        src_lang: str,
        tgt_lang: str,
        voice: str = "",
    ) -> LiveSessionInfo:
        try:
            from engines.live.pipeline import LiveTranslationPipeline
            from engines.live.preflight import preflight_live

            pf = preflight_live(require_stt=True)
            if not pf.get("ok"):
                return LiveSessionInfo(
                    session_id="",
                    status="error",
                    error="; ".join(pf.get("issues") or ["preflight failed"]),
                    meta={"preflight": pf, "readiness": "YELLOW"},
                )
            pipe = LiveTranslationPipeline(self.app_dir)
            sid = pipe.start(
                source_uri,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                voice=voice,
            )
            return LiveSessionInfo(
                session_id=sid,
                status="running",
                meta={
                    "adapter": "live.pipeline",
                    "readiness": self.readiness,
                    "preflight": pf,
                },
            )
        except Exception as exc:
            return LiveSessionInfo(session_id="", status="error", error=str(exc))
    def stop(self, session_id: str) -> bool:
        try:
            from engines.live.pipeline import LiveTranslationPipeline

            LiveTranslationPipeline(self.app_dir).stop(session_id)
            return True
        except Exception:
            return False

    def events(self, session_id: str, after: int = 0) -> Iterator[dict[str, Any]]:
        try:
            from engines.live.pipeline import LiveTranslationPipeline

            pipe = LiveTranslationPipeline(self.app_dir)
            for ev in pipe.subscribe_events(session_id, after=after, timeout_sec=0.1):
                yield ev
        except Exception:
            return iter(())


class LiveStub(LiveInterface):
    """Placeholder when live module explicitly disabled."""

    readiness = "RED"

    def start(
        self,
        source_uri: str,
        *,
        src_lang: str,
        tgt_lang: str,
        voice: str = "",
    ) -> LiveSessionInfo:
        return LiveSessionInfo(
            session_id="",
            status="disabled",
            error="Live module disabled — enable VM_LIVE_ENABLED / platform live",
        )

    def stop(self, session_id: str) -> bool:
        return False

    def events(self, session_id: str, after: int = 0) -> Iterator[dict[str, Any]]:
        return iter(())


def get_live_interface(app_dir: Path, *, enabled: bool = True) -> LiveInterface:
    if not enabled:
        return LiveStub()
    return LivePipelineAdapter(app_dir)
