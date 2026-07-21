"""P201 — TranslationBackend interface (model-agnostic)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackendCapabilities:
    offline: bool = True
    multi_variant: bool = False
    context_aware: bool = False
    languages: list[str] = field(default_factory=list)
    max_chars: int = 8000

    def to_dict(self) -> dict[str, Any]:
        return {
            "offline": self.offline,
            "multi_variant": self.multi_variant,
            "context_aware": self.context_aware,
            "languages": list(self.languages),
            "max_chars": self.max_chars,
        }


class TranslationBackend(ABC):
    """Every MT/LLM engine plugs in through this contract only."""

    id: str = "base"
    name: str = "Base"
    version: str = "1"

    @abstractmethod
    def initialize(self) -> None:
        ...

    @abstractmethod
    def translate(
        self,
        text: str,
        *,
        src_lang: str,
        tgt_lang: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        ...

    @abstractmethod
    def health_check(self) -> bool:
        ...

    @abstractmethod
    def shutdown(self) -> None:
        ...

    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        ...
