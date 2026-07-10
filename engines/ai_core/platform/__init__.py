"""TubeDub AI Core Platform — Master Spec v3.0 Stage 1 foundation."""

from engines.ai_core.platform.agent_protocol import AgentProtocolMixin
from engines.ai_core.platform.ai_bus import (
    AIBus,
    EVENT_MANIFEST_PUBLISHED,
    EVENT_MEMORY_UPDATED,
    EVENT_RECOVERY_ROUTED,
    EVENT_STATE_UPDATED,
    get_bus,
    register_recovery_handler,
    reset_bus,
    save_bus_snapshot,
)
from engines.ai_core.platform.capability_registry import (
    CapabilityStatus,
    build_registry,
    get_capability,
)
from engines.ai_core.platform.feature_registry import (
    is_platform_feature_enabled,
    list_platform_features,
)
from engines.ai_core.platform.project_state import (
    AGENT_WRITE_SCOPES,
    ProjectStateGuard,
    freeze_manifest,
)
from engines.ai_core.platform.versions import (
    AI_BUS_VERSION,
    GLOBAL_SKILL_VERSION,
    MANIFEST_VERSION,
    PLATFORM_SPEC_VERSION,
    PROTOCOL_VERSION,
    STATE_VERSION,
    agent_protocol_header,
    platform_versions,
)

__all__ = [
    "AIBus",
    "AgentProtocolMixin",
    "CapabilityStatus",
    "PLATFORM_SPEC_VERSION",
    "AI_BUS_VERSION",
    "PROTOCOL_VERSION",
    "MANIFEST_VERSION",
    "STATE_VERSION",
    "GLOBAL_SKILL_VERSION",
    "AGENT_WRITE_SCOPES",
    "ProjectStateGuard",
    "build_registry",
    "get_capability",
    "get_bus",
    "reset_bus",
    "save_bus_snapshot",
    "register_recovery_handler",
    "platform_versions",
    "agent_protocol_header",
    "freeze_manifest",
    "is_platform_feature_enabled",
    "list_platform_features",
    "EVENT_MANIFEST_PUBLISHED",
    "EVENT_STATE_UPDATED",
    "EVENT_RECOVERY_ROUTED",
    "EVENT_MEMORY_UPDATED",
]
