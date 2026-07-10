"""Effect plugin interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EffectContext:
    sample_rate: int = 44100
    channels: int = 1
    lang: str = "uk"
    segment_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessResult:
    output_path: str
    duration_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class EffectModule(abc.ABC):
    """Base class for all Dub Studio DSP plugins (future VST/AU bridge)."""

    plugin_id: str = "base"
    label: str = "Base"
    category: str = "utility"

    @abc.abstractmethod
    def process(
        self,
        input_path: Path,
        output_path: Path,
        *,
        params: dict[str, Any] | None = None,
        ctx: EffectContext | None = None,
    ) -> ProcessResult:
        ...

    def default_params(self) -> dict[str, Any]:
        return {}

    def schema(self) -> dict[str, Any]:
        return {"plugin_id": self.plugin_id, "label": self.label, "params": self.default_params()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "label": self.label,
            "category": self.category,
            "schema": self.schema(),
        }
