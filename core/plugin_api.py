"""Plugin API — unified interface for VideoMonster V2 extensions (TZ #9 §2–§5).

The core knows only these interfaces. All new capabilities are added as plugins
without modifying Event Bus, Orchestrator, Pipeline, or other restricted modules.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# Core API version — plugins declare minimum_api against this (§12).
CORE_API_VERSION = "1.0.0"
CORE_API_MAJOR = 1


class PluginState(str, Enum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    ENABLED = "enabled"
    DISABLED = "disabled"
    FAILED = "failed"
    INCOMPATIBLE = "incompatible"


class Capability(str, Enum):
    """Declared plugin capabilities (§5). Orchestrator works with these, not plugins."""
    TRANSLATION = "translation"
    TTS = "tts"
    STT = "stt"
    VOICE_CLONE = "voice_clone"
    LIP_SYNC = "lip_sync"
    SUBTITLE = "subtitle"
    EXPORT = "export"
    REVIEW = "review"
    OCR = "ocr"
    NOISE_REDUCTION = "noise_reduction"
    MIX = "mix"
    TIMING = "timing"
    MEMORY = "memory"
    UTILITY = "utility"


class ExecutionMode(str, Enum):
    """Future distributed processing support (§17)."""
    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"


@dataclass
class PluginPermissions:
    """Explicit permission declarations (§13)."""
    file: bool = False
    network: bool = False
    gpu: bool = False
    audio: bool = False
    video: bool = False
    memory: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> PluginPermissions:
        d = d or {}
        return cls(
            file=bool(d.get("file")),
            network=bool(d.get("network")),
            gpu=bool(d.get("gpu")),
            audio=bool(d.get("audio")),
            video=bool(d.get("video")),
            memory=bool(d.get("memory")),
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "file": self.file,
            "network": self.network,
            "gpu": self.gpu,
            "audio": self.audio,
            "video": self.video,
            "memory": self.memory,
        }


@dataclass
class PluginManifest:
    """plugin.json schema (§4)."""
    name: str
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    minimum_api: str = "1.0.0"
    dependencies: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    permissions: PluginPermissions = field(default_factory=PluginPermissions)
    python_packages: list[str] = field(default_factory=list)
    entry_point: str = "plugin.py"
    execution_mode: str = ExecutionMode.LOCAL.value
    remote_endpoint: str = ""
    deprecated: bool = False
    replaces: str = ""

    @classmethod
    def from_json(cls, path: Path) -> PluginManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=str(data.get("name") or path.parent.name),
            version=str(data.get("version") or "0.1.0"),
            author=str(data.get("author") or ""),
            description=str(data.get("description") or ""),
            minimum_api=str(data.get("minimum_api") or "1.0.0"),
            dependencies=list(data.get("dependencies") or []),
            capabilities=list(data.get("capabilities") or []),
            permissions=PluginPermissions.from_dict(data.get("permissions")),
            python_packages=list(data.get("python_packages") or []),
            entry_point=str(data.get("entry_point") or "plugin.py"),
            execution_mode=str(data.get("execution_mode") or ExecutionMode.LOCAL.value),
            remote_endpoint=str(data.get("remote_endpoint") or ""),
            deprecated=bool(data.get("deprecated")),
            replaces=str(data.get("replaces") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "minimum_api": self.minimum_api,
            "dependencies": list(self.dependencies),
            "capabilities": list(self.capabilities),
            "permissions": self.permissions.to_dict(),
            "python_packages": list(self.python_packages),
            "entry_point": self.entry_point,
            "execution_mode": self.execution_mode,
            "remote_endpoint": self.remote_endpoint,
            "deprecated": self.deprecated,
            "replaces": self.replaces,
        }


@dataclass
class PluginHealth:
    ok: bool = True
    message: str = "ok"
    latency_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "latency_ms": round(self.latency_ms, 1),
            "details": dict(self.details),
        }


class VMPlugin(ABC):
    """Required plugin interface (§2)."""

    @abstractmethod
    def initialize(self, context: dict[str, Any]) -> None:
        """Called once when the plugin is loaded."""

    @abstractmethod
    def shutdown(self) -> None:
        """Called when the plugin is disabled or unloaded."""

    @abstractmethod
    def health(self) -> PluginHealth:
        """Return current health status."""

    @abstractmethod
    def capabilities(self) -> list[str]:
        """Return capability strings this plugin provides."""

    @abstractmethod
    def version(self) -> str:
        """Return plugin version string."""

    @abstractmethod
    def dependencies(self) -> list[str]:
        """Return plugin-level dependency names."""

    def execution_mode(self) -> str:
        """Local / remote / hybrid (§17)."""
        return ExecutionMode.LOCAL.value

    def remote_endpoint(self) -> str:
        """Optional remote endpoint for distributed execution (§17)."""
        return ""


def parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(version).strip().split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def version_compatible(required: str, available: str = CORE_API_VERSION) -> bool:
    """True if available API version satisfies required minimum (§12)."""
    return parse_version(available) >= parse_version(required)
