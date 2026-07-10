"""Abstract translator interface and registry for Translation Agent v1.0."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTranslator(ABC):
    """Pluggable MT backend."""

    name: str = "base"

    @abstractmethod
    def translate(self, text: str, source: str, target: str) -> str:
        """Translate text between language codes."""

    @abstractmethod
    def is_available(self) -> bool:
        """True when this backend can be used right now."""


class TranslatorRegistry:
    """Select translators from manifest capability matrix and fallback chain."""

    def __init__(self, capability_matrix: dict[str, Any] | None = None):
        self._cap = capability_matrix or {}
        self._translators: list[BaseTranslator] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        from engines.ai_core.translation_agent.translators.argos_translator import (
            ArgosTranslator,
        )
        from engines.ai_core.translation_agent.translators.cloud_translator import (
            CloudTranslator,
        )
        from engines.ai_core.translation_agent.translators.deep_translator import (
            DeepTranslatorWrapper,
        )

        self._translators = [
            CloudTranslator(),
            ArgosTranslator(),
            DeepTranslatorWrapper(),
        ]
        self._loaded = True

    def select_best(self, capability_matrix: dict[str, Any] | None = None) -> BaseTranslator:
        """Pick highest-priority available translator."""
        self._ensure_loaded()
        cap = capability_matrix or self._cap
        for tr in self._translators:
            if not tr.is_available():
                continue
            if tr.name == "cloud" and not cap.get("llm"):
                # Cloud still usable when explicit API keys exist
                if not tr.is_available():
                    continue
            return tr
        return self._translators[-1]

    def fallback_chain(self) -> list[BaseTranslator]:
        """Cloud → Argos → deep-translator (available only)."""
        self._ensure_loaded()
        return [t for t in self._translators if t.is_available()]
