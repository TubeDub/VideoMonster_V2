"""Dub Studio data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TrackKind(str, Enum):
    VIDEO = "video"
    ORIGINAL = "original"
    TTS = "tts"
    USER_VOICE = "user_voice"
    MUSIC = "music"
    FX = "fx"
    AUX = "aux"


class ContainerStatus(str, Enum):
    GREEN = "green"  # 0-90%
    YELLOW = "yellow"  # 90-100%
    RED = "red"  # >100%


class EmotionTag(str, Enum):
    NEUTRAL = "NEUTRAL"
    HAPPY = "HAPPY"
    ANGRY = "ANGRY"
    SAD = "SAD"
    WHISPER = "WHISPER"
    SHOUTING = "SHOUTING"
    IRONIC = "IRONIC"


@dataclass
class FxSlot:
    plugin_id: str
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SegmentVersion:
    version_id: str
    label: str
    audio_path: str = ""
    source: str = "tts"  # tts | user | import
    created_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StudioSegment:
    segment_id: str
    index: int
    text: str
    start_ms: int
    end_ms: int
    hard_anchor_ms: int = 0
    container_ms: int = 0
    tts_ms: int = 0
    stretch_ratio: float = 1.0
    container_status: str = ContainerStatus.GREEN.value
    emotion: str = EmotionTag.NEUTRAL.value
    emotion_confidence: float = 0.0
    emotion_manual: bool = False
    tts_params: dict[str, Any] = field(default_factory=dict)
    active_version_id: str = ""
    versions: list[SegmentVersion] = field(default_factory=list)
    fx_chain: list[FxSlot] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["versions"] = [v.to_dict() if hasattr(v, "to_dict") else v for v in self.versions]
        d["fx_chain"] = [f.to_dict() if hasattr(f, "to_dict") else f for f in self.fx_chain]
        return d


@dataclass
class StudioTrack:
    track_id: str
    kind: str
    label: str
    muted: bool = False
    solo: bool = False
    volume: float = 1.0
    pan: float = 0.0
    monitor: bool = False
    record_enabled: bool = False
    fx_chain: list[FxSlot] = field(default_factory=list)
    plugin_slots: list[FxSlot] = field(default_factory=list)
    clips: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fx_chain"] = [f.to_dict() if hasattr(f, "to_dict") else f for f in self.fx_chain]
        d["plugin_slots"] = [f.to_dict() if hasattr(f, "to_dict") else f for f in self.plugin_slots]
        return d


@dataclass
class DubProject:
    project_id: str
    title: str
    video_path: str = ""
    duration_ms: int = 0
    tracks: list[StudioTrack] = field(default_factory=list)
    segments: list[StudioSegment] = field(default_factory=list)
    master_fx: list[FxSlot] = field(default_factory=list)
    created_ms: int = 0
    updated_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "video_path": self.video_path,
            "duration_ms": self.duration_ms,
            "tracks": [t.to_dict() for t in self.tracks],
            "segments": [s.to_dict() for s in self.segments],
            "master_fx": [f.to_dict() for f in self.master_fx],
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "meta": self.meta,
        }
