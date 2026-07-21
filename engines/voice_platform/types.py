"""Voice Platform types — Master Spec Part 7 (P601–P625)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


EMOTIONS: tuple[str, ...] = (
    "joy",
    "sadness",
    "fear",
    "surprise",
    "irony",
    "sarcasm",
    "calm",
    "anger",
)

# Alias map from Semantic Core / tagger labels
EMOTION_ALIASES: dict[str, str] = {
    "happy": "joy",
    "happiness": "joy",
    "sad": "sadness",
    "afraid": "fear",
    "scared": "fear",
    "surprised": "surprise",
    "neutral": "calm",
    "angry": "anger",
    "rage": "anger",
}


@dataclass
class VoiceCapabilities:
    languages: list[str] = field(default_factory=list)
    cloning: bool = False
    prosody: bool = False
    emotion: bool = False
    ssml: bool = False
    streaming: bool = False
    offline: bool = True
    sample_rates: list[int] = field(default_factory=lambda: [16000, 22050, 24000])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoiceEntry:
    """P603 — Voice Registry catalog row."""

    voice_uuid: str
    provider: str
    language: str = "en"
    gender: str = "unknown"
    age: str = "adult"
    style: str = "neutral"
    emotion_support: list[str] = field(default_factory=lambda: list(EMOTIONS))
    sample_rate: int = 24000
    quality: str = "standard"
    speed_range: tuple[float, float] = (0.75, 1.25)
    pitch_range: tuple[float, float] = (-10.0, 10.0)
    prosody_support: bool = True
    cloning_support: bool = False
    license: str = "unknown"
    display_name: str = ""
    external_id: str = ""  # provider-native voice id (e.g. Edge Neural name)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["speed_range"] = list(self.speed_range)
        d["pitch_range"] = list(self.pitch_range)
        return d


@dataclass
class StyleProfile:
    """P604 — configurable content style profile."""

    name: str
    speech_rate: float = 1.0
    emotion_default: str = "calm"
    pitch_bias: float = 0.0
    diction: str = "neutral"  # clear | soft | energetic | expressive
    pause_scale: float = 1.0
    prosody_intensity: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpeakerIdentity:
    """P607 — character voice identity."""

    speaker_uuid: str
    voice_uuid: str
    style_profile: str = "Documentary"
    emotion_profile: str = "calm"
    history: list[dict[str, Any]] = field(default_factory=list)
    consistency_score: float = 100.0
    language: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoicePlan:
    """P605 — pre-TTS plan for one Speech Unit."""

    speech_uuid: str
    speaker_uuid: str
    voice_uuid: str
    provider: str
    style: str = "Documentary"
    tempo: float = 1.0
    emotion: str = "calm"
    prosody: dict[str, Any] = field(default_factory=dict)
    language: str = ""
    external_voice_id: str = ""
    rate: str | None = None
    pitch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PhonemeSpec:
    """P611."""

    ipa: str
    phoneme: str
    duration_ms: float
    position: int
    stress: float = 0.0
    word: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VisemeSpec:
    """P612."""

    viseme: str
    mouth_open: float
    mouth_close: float
    jaw: float
    lip_rounding: float
    start_ms: float
    end_ms: float
    phoneme: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LipSyncData:
    """P613 — data only, no animation."""

    speech_uuid: str
    phonemes: list[PhonemeSpec] = field(default_factory=list)
    visemes: list[VisemeSpec] = field(default_factory=list)
    version: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "speech_uuid": self.speech_uuid,
            "version": self.version,
            "phonemes": [p.to_dict() for p in self.phonemes],
            "visemes": [v.to_dict() for v in self.visemes],
        }


@dataclass
class SynthesisRequest:
    text: str
    voice_uuid: str
    speech_uuid: str = ""
    provider: str | None = None
    language: str = ""
    emotion: str = "calm"
    rate: str | None = None
    pitch: str | None = None
    tempo: float = 1.0
    output_path: str = ""
    contract_version: str = "7.0"
    allow_cache: bool = True
    clone_ref_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SynthesisResult:
    ok: bool
    output_path: str = ""
    provider: str = ""
    voice_uuid: str = ""
    speech_uuid: str = ""
    cached: bool = False
    elapsed_ms: float = 0.0
    error: str = ""
    quality: dict[str, Any] = field(default_factory=dict)
    lipsync: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
