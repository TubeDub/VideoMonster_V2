"""Audio plugin interface — process(audio, params) -> audio (TZ §11)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PluginParams:
    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class AudioPlugin(ABC):
    plugin_id: str = "base"
    label: str = "Base Plugin"
    i18n_key: str = ""

    @abstractmethod
    def process(self, audio_path: str | Path, params: PluginParams | None = None) -> str:
        """Return path to processed audio file."""

    def describe(self) -> dict[str, Any]:
        out = {"plugin_id": self.plugin_id, "label": self.label, "id": self.plugin_id}
        if self.i18n_key:
            out["i18n_key"] = self.i18n_key
        return out


class PassThroughPlugin(AudioPlugin):
    """Default pass-through with logging."""

    def __init__(self, plugin_id: str, label: str) -> None:
        self.plugin_id = plugin_id
        self.label = label

    def process(self, audio_path: str | Path, params: PluginParams | None = None) -> str:
        p = Path(audio_path)
        logger.info("plugin %s: pass-through %s params=%s", self.plugin_id, p.name, params)
        return str(p)
