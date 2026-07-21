"""Enterprise Architecture types — Master Spec Part 9 (P801–P820)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
import uuid
import time


ENTERPRISE_VERSION = "9.0.0"
MASTER_SPEC_COMPLETE = True


class ConfigDomain(str, Enum):
    """P801 — configuration domains separate from code."""

    PIPELINE = "Pipeline"
    TRANSLATION = "Translation"
    DUB = "Dub"
    SCHEDULER = "Scheduler"
    DECISION = "Decision"
    TTS = "TTS"
    DIAGNOSTICS = "Diagnostics"
    STUDIO = "Studio"
    PLUGINS = "Plugins"
    CLOUD = "Cloud"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class ComputeNodeKind(str, Enum):
    """P807 — logical node kinds (logic unchanged)."""

    GPU = "gpu"
    CPU = "cpu"
    ANY = "any"


@dataclass
class ConfigurationRecord:
    """P802 — versioned configuration."""

    domain: str
    configuration_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: str = "1.0.0"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    migration_version: int = 1
    compatibility: str = ">=6.0.0"
    rollback_point: str = ""
    profile: str = "default"
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineVersionBundle:
    """P804 — reproducible pipeline version fingerprint."""

    pipeline_version: str = "6.0.0"
    contracts_version: str = "1.0.0"
    semantic_version: str = "3.0.0"
    decision_version: str = "4.0.0"
    dub_version: str = "5.0.0"
    scheduler_version: str = "2.0.0"
    tts_version: str = "7.0.0"
    diagnostics_version: str = "6.0.0"
    platform_sdk_version: str = "8.0.0"
    enterprise_version: str = ENTERPRISE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineTask:
    """P808 — independent pipeline stage task."""

    task_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    dependencies: list[str] = field(default_factory=list)
    priority: int = 100
    retry_policy: dict[str, Any] = field(default_factory=lambda: {"max": 3, "backoff_sec": 1.0})
    status: str = TaskStatus.PENDING.value
    metrics: dict[str, Any] = field(default_factory=dict)
    node_kind: str = ComputeNodeKind.ANY.value
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
