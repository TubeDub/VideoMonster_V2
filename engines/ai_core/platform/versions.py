"""TubeDub AI Core platform versioning (Master Spec v3.0 §21)."""

from __future__ import annotations

from typing import Any

# Frozen platform identifiers — bump on breaking contract changes.
PLATFORM_SPEC_VERSION = "3.0"
AI_BUS_VERSION = "1.0"
PROTOCOL_VERSION = "1.0"
MANIFEST_VERSION = "3.0"
STATE_VERSION = "1.0"
GLOBAL_SKILL_VERSION = "1.0"


def platform_versions() -> dict[str, str]:
    return {
        "platform_spec_version": PLATFORM_SPEC_VERSION,
        "ai_bus_version": AI_BUS_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "manifest_version": MANIFEST_VERSION,
        "state_version": STATE_VERSION,
        "global_skill_version": GLOBAL_SKILL_VERSION,
    }


def agent_protocol_header(agent_id: str, agent_version: str) -> dict[str, Any]:
    """Version block every agent must attach to results (§21)."""
    return {
        "agent_id": agent_id,
        "agent_version": agent_version,
        **platform_versions(),
    }
