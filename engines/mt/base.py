"""Unified machine translation engine interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MTResult:
    text: str
    engine_id: str
    engine_version: str = ""
    offline: bool = True
    elapsed_ms: float = 0.0
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class BaseMTEngine(ABC):
    """Pluggable MT engine — core never depends on implementation details."""

    id: str = "base"
    name: str = "Base"
    version: str = "0"
    offline: bool = True
    priority: int = 50

    @abstractmethod
    def is_available(self) -> bool:
        """Runtime check: deps installed, models reachable."""

    @abstractmethod
    def supports_pair(self, src_lang: str, tgt_lang: str) -> bool:
        """True if this engine can translate src→tgt."""

    @abstractmethod
    def translate(self, text: str, src_lang: str, tgt_lang: str) -> MTResult:
        """Translate text; empty result.text on failure."""

    def estimate_memory_mb(self) -> int:
        return 0
