"""TTS engine protocol — pluggable synthesis backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class TTSResult:
    ok: bool
    output_path: str = ""
    error: str = ""
    engine_id: str = ""
    elapsed_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class TTSEngineInfo:
    id: str
    name: str
    mode: str
    provider: str
    description: str = ""
    supports_stress: bool = False
    supports_ssml: bool = False
    available: bool = False
    config_keys: list[str] = field(default_factory=list)


class BaseTTSEngine(Protocol):
    id: str
    name: str
    mode: str
    supports_stress: bool
    supports_ssml: bool

    def is_available(self) -> bool: ...

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult: ...
