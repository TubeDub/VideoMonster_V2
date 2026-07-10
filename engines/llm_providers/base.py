"""LLM provider abstraction — TubeDub AI Backend.

Each provider represents a model *family* (DeepSeek, Llama, Qwen) backed by the
same OpenAI-compatible local server (Ollama). The rest of TubeDub talks only to
``engines.llm_providers.registry`` and ``resolve_llm_model`` — never to a
specific family directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMProvider(ABC):
    """Model family metadata and selection logic."""

    family_id: str
    display_name: str
    default_model: str
    candidate_models: tuple[str, ...] = field(default_factory=tuple)

    @abstractmethod
    def matches_installed(self, model_name: str) -> bool:
        """True when an Ollama tag belongs to this family."""

    def resolve_installed_model(self, available: list[str]) -> str | None:
        """Pick the best installed tag for this family, or None."""
        if not available:
            return None
        low = {m.lower(): m for m in available}
        for candidate in self.candidate_models:
            c = candidate.lower()
            if c in low:
                return low[c]
            for lname, original in low.items():
                if c in lname or lname.startswith(c.split(":")[0]):
                    return original
        for name in available:
            if self.matches_installed(name):
                return name
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.family_id,
            "label": self.display_name,
            "default_model": self.default_model,
            "installed": False,
        }
