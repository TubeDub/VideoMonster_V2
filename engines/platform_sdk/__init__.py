"""Platform SDK • Plugin System • API • Cloud • Ecosystem — Master Spec Part 8."""

from __future__ import annotations

from engines.platform_sdk.engine import bootstrap_platform, platform_status
from engines.platform_sdk.public_api import PublicAPI, get_public_api
from engines.platform_sdk.types import (
    PLATFORM_SDK_VERSION,
    ExtensionPoint,
    Permission,
    PluginDescriptor,
    PluginLifecycle,
    PlatformEvent,
    TrustLevel,
)

__all__ = [
    "PLATFORM_SDK_VERSION",
    "ExtensionPoint",
    "Permission",
    "PlatformEvent",
    "PluginDescriptor",
    "PluginLifecycle",
    "PublicAPI",
    "TrustLevel",
    "bootstrap_platform",
    "get_public_api",
    "platform_status",
]
