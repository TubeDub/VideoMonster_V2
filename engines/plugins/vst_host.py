"""VST2/VST3 host — Phase hook abstract interface (no native load in this build)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VstPluginInfo:
    plugin_id: str
    name: str
    path: str
    format: str  # vst2 | vst3
    vendor: str = ""
    params: dict[str, Any] = field(default_factory=dict)


class VstHost(ABC):
    """Abstract VST host — concrete implementation requires native bridge."""

    @abstractmethod
    def scan_plugins(self, search_paths: list[str | Path] | None = None) -> list[VstPluginInfo]:
        """Discover installed VST2/VST3 plugins."""

    @abstractmethod
    def load_plugin(self, plugin_path: str | Path) -> str:
        """Load plugin instance; return opaque handle id."""

    @abstractmethod
    def process_audio(
        self,
        handle_id: str,
        audio_path: str | Path,
        *,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Process audio through loaded VST; return output path."""

    @abstractmethod
    def unload_plugin(self, handle_id: str) -> None:
        """Release plugin instance."""


class StubVstHost(VstHost):
    """
    Phase 1 stub — documents the contract without loading native VST binaries.
    Built-in ffmpeg plugins (registry) process loudness/compressor during export.
    """

    def scan_plugins(self, search_paths: list[str | Path] | None = None) -> list[VstPluginInfo]:
        logger.info("VST host: scan skipped (Phase 2 native bridge not bundled)")
        return []

    def load_plugin(self, plugin_path: str | Path) -> str:
        raise NotImplementedError(
            "VST load is not available in this build. Use built-in export plugins "
            "(Loudness Normalize, Simple Compressor) via Dub Studio sidebar."
        )

    def process_audio(
        self,
        handle_id: str,
        audio_path: str | Path,
        *,
        params: dict[str, Any] | None = None,
    ) -> str:
        raise NotImplementedError("VST process requires Phase 2 native host")

    def unload_plugin(self, handle_id: str) -> None:
        pass


def get_vst_host() -> VstHost:
    return StubVstHost()
