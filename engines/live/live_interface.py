"""Live translation interface stub — RED status (TZ §15)."""

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
    """Adapter to existing engines.live.pipeline (partial implementation)."""

    readiness = "RED"

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

            pipe = LiveTranslationPipeline(self.app_dir)
            sid = pipe.start(
                source_uri,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                voice=voice,
            )
            return LiveSessionInfo(session_id=sid, status="starting", meta={"adapter": "live.pipeline"})
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
    """Placeholder when live module disabled."""

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
            error="Live module RED — not available in production",
        )

    def stop(self, session_id: str) -> bool:
        return False

    def events(self, session_id: str, after: int = 0) -> Iterator[dict[str, Any]]:
        return iter(())
