"""TubeDub Event Bus — typed event definitions (TZ §2)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict


class EventType(str, Enum):
    """Canonical event type names for the dubbing pipeline."""

    PIPELINE_STARTED = "pipeline_started"
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_FAILED = "pipeline_failed"

    CLEANER_REQUESTED = "cleaner_requested"
    SEGMENTS_ALIGNED = "segments_aligned"
    AUDIO_CLEANED = "audio_cleaned"

    TRANSLATION_REQUESTED = "translation_requested"
    TRANSLATION_COMPLETED = "translation_completed"

    TIMING_REQUESTED = "timing_requested"
    TIMING_COMPLETED = "timing_completed"

    VOICE_REQUESTED = "voice_requested"
    VOICE_COMPLETED = "voice_completed"

    MIX_REQUESTED = "mix_requested"
    MIX_COMPLETED = "mix_completed"

    EXPORT_REQUESTED = "export_requested"
    EXPORT_COMPLETED = "export_completed"

    AGENT_ERROR = "agent_error"
    SHUTDOWN = "shutdown"


# Typed payload shapes (TZ §2 — no arbitrary dicts at API boundary).
class CleanerPayload(TypedDict, total=False):
    segments: list[str]
    timing_map: list[Any]
    source_text: str


class TranslationPayload(TypedDict, total=False):
    source_segments: list[str]
    timing_map: list[Any]
    source_lang: str
    target_lang: str
    translate_meta: list[Any]
    segments: list[str]


class TimingPayload(TypedDict, total=False):
    segments: list[str]
    timing_map: list[Any]
    source_segments: list[str]
    target_lang: str
    records: list[Any]


class VoicePayload(TypedDict, total=False):
    segments: list[str]
    timing_map: list[Any]
    target_lang: str
    tts_engine: str
    voice: str
    rate: str
    pitch: str
    tts_files: list[Any]


class MixPayload(TypedDict, total=False):
    task_id: str
    force: bool


class ExportPayload(TypedDict, total=False):
    task_id: str
    output_path: str
    timed_audio_path: str


class AgentErrorPayload(TypedDict, total=False):
    agent: str
    chunk_id: int
    error: str
    recoverable: bool


@dataclass(frozen=True)
class BusEvent:
    """Strict event envelope — every bus message uses this format."""

    event_id: str
    event_type: str
    project_id: str
    chunk_id: int
    timestamp: float
    payload: dict[str, Any]
    priority: int = 0
    source_agent: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "project_id": self.project_id,
            "chunk_id": self.chunk_id,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
            "priority": self.priority,
            "source_agent": self.source_agent,
        }

    @classmethod
    def create(
        cls,
        event_type: str | EventType,
        *,
        project_id: str,
        chunk_id: int = 0,
        payload: dict[str, Any] | None = None,
        priority: int = 0,
        source_agent: str = "",
    ) -> BusEvent:
        et = event_type.value if isinstance(event_type, EventType) else str(event_type)
        if not et:
            raise ValueError("event_type is required")
        if not project_id:
            raise ValueError("project_id is required")
        raw = payload or {}
        if not isinstance(raw, dict):
            raise TypeError("payload must be a dict")
        return cls(
            event_id=str(uuid.uuid4()),
            event_type=et,
            project_id=str(project_id),
            chunk_id=int(chunk_id),
            timestamp=time.time(),
            payload=dict(raw),
            priority=int(priority),
            source_agent=str(source_agent or ""),
        )


# Event chain — preserves existing algorithm order (translate → align → timing → …).
AGENT_SUBSCRIPTIONS: dict[str, tuple[str, ...]] = {
    "translator": (
        EventType.TRANSLATION_REQUESTED.value,
        EventType.PIPELINE_STARTED.value,
    ),
    "cleaner": (EventType.TRANSLATION_COMPLETED.value, EventType.CLEANER_REQUESTED.value),
    "timing": (EventType.SEGMENTS_ALIGNED.value, EventType.TIMING_REQUESTED.value),
    "voice": (EventType.TIMING_COMPLETED.value, EventType.VOICE_REQUESTED.value),
    "mix": (EventType.VOICE_COMPLETED.value, EventType.MIX_REQUESTED.value),
    "export": (EventType.MIX_COMPLETED.value, EventType.EXPORT_REQUESTED.value),
}

AGENT_OUTPUT_EVENT: dict[str, str] = {
    "translator": EventType.TRANSLATION_COMPLETED.value,
    "cleaner": EventType.SEGMENTS_ALIGNED.value,
    "timing": EventType.TIMING_COMPLETED.value,
    "voice": EventType.VOICE_COMPLETED.value,
    "mix": EventType.MIX_COMPLETED.value,
    "export": EventType.EXPORT_COMPLETED.value,
}
