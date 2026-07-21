"""Platform SDK types — Master Spec Part 8 (P701–P726)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


PLATFORM_SDK_VERSION = "8.0.0"
CORE_PROTECTED = True  # P701


class PluginLifecycle(str, Enum):
    """P704 — fixed lifecycle; no arbitrary states."""

    INSTALLED = "Installed"
    VERIFIED = "Verified"
    LOADED = "Loaded"
    INITIALIZED = "Initialized"
    RUNNING = "Running"
    PAUSED = "Paused"
    STOPPED = "Stopped"
    REMOVED = "Removed"


LIFECYCLE_ORDER: tuple[PluginLifecycle, ...] = (
    PluginLifecycle.INSTALLED,
    PluginLifecycle.VERIFIED,
    PluginLifecycle.LOADED,
    PluginLifecycle.INITIALIZED,
    PluginLifecycle.RUNNING,
    PluginLifecycle.PAUSED,
    PluginLifecycle.STOPPED,
    PluginLifecycle.REMOVED,
)


class Permission(str, Enum):
    """P705 — explicit permissions."""

    READ_PROJECT = "Read Project"
    WRITE_PROJECT = "Write Project"
    READ_AUDIO = "Read Audio"
    GENERATE_AUDIO = "Generate Audio"
    READ_TRANSLATION = "Read Translation"
    READ_TIMELINE = "Read Timeline"
    EXPORT = "Export"
    INTERNET = "Internet"
    CLOUD = "Cloud"
    FILESYSTEM = "Filesystem"


class ExtensionPoint(str, Enum):
    """P708 — extension points."""

    ASR = "ASR"
    TRANSLATION = "Translation"
    DECISION = "Decision"
    DUB = "Dub"
    SCHEDULER = "Scheduler"
    ALIGNMENT = "Alignment"
    TTS = "TTS"
    STUDIO = "Studio"
    EXPORT = "Export"
    DIAGNOSTICS = "Diagnostics"


class TrustLevel(str, Enum):
    """P713 — signature trust display."""

    TRUSTED = "Trusted"
    VERIFIED = "Verified"
    UNKNOWN = "Unknown"
    BLOCKED = "Blocked"


class PlatformEvent(str, Enum):
    """P707 — canonical platform events."""

    PROJECT_OPENED = "Project Opened"
    TRANSLATION_FINISHED = "Translation Finished"
    SEMANTIC_LOCKED = "Semantic Locked"
    SCHEDULER_FINISHED = "Scheduler Finished"
    MERGE_FINISHED = "Merge Finished"
    EXPORT_FINISHED = "Export Finished"
    PIPELINE_FINISHED = "Pipeline Finished"
    PROJECT_FAILED = "Project Failed"
    DIAGNOSTICS_READY = "Diagnostics Ready"
    RELEASE_READY = "Release Ready"
    PLUGIN_STATE_CHANGED = "Plugin State Changed"


class MarketplaceKind(str, Enum):
    """P721."""

    TTS = "TTS"
    TRANSLATION = "Translation"
    GLOSSARY = "Glossary"
    VOICE_PACK = "Voice Packs"
    EXPORTER = "Exporters"
    DIAGNOSTICS = "Diagnostics"
    THEME = "Themes"
    AUTOMATION = "Automation"


class TeamRole(str, Enum):
    """P717."""

    TRANSLATOR = "translator"
    EDITOR = "editor"
    DUB_DIRECTOR = "dub_director"
    SOUND_ENGINEER = "sound_engineer"
    ADMIN = "admin"
    VIEWER = "viewer"


@dataclass
class PluginDescriptor:
    """P702 — official SDK plugin descriptor."""

    plugin_id: str
    version: str
    author: str = ""
    description: str = ""
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    compatibility: dict[str, Any] = field(default_factory=dict)
    lifecycle: str = PluginLifecycle.INSTALLED.value
    extension_points: list[str] = field(default_factory=list)
    trust: str = TrustLevel.UNKNOWN.value
    min_core_version: str = "6.0.0"
    max_core_version: str = "99.0.0"
    contracts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginDescriptor":
        return cls(
            plugin_id=str(data.get("plugin_id") or data.get("name") or data.get("id") or ""),
            version=str(data.get("version") or "0.1.0"),
            author=str(data.get("author") or ""),
            description=str(data.get("description") or ""),
            permissions=list(data.get("permissions") or []),
            dependencies=list(data.get("dependencies") or []),
            capabilities=list(data.get("capabilities") or []),
            compatibility=dict(data.get("compatibility") or {}),
            lifecycle=str(data.get("lifecycle") or PluginLifecycle.INSTALLED.value),
            extension_points=list(data.get("extension_points") or []),
            trust=str(data.get("trust") or TrustLevel.UNKNOWN.value),
            min_core_version=str(
                data.get("min_core_version")
                or (data.get("compatibility") or {}).get("min_core")
                or data.get("minimum_api")
                or "6.0.0"
            ),
            max_core_version=str(
                data.get("max_core_version")
                or (data.get("compatibility") or {}).get("max_core")
                or "99.0.0"
            ),
            contracts=list(data.get("contracts") or []),
        )


@dataclass
class PluginHealthReport:
    """P724."""

    plugin_id: str
    ok: bool = True
    version: str = ""
    errors: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
