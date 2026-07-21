"""Platform SDK orchestrator — Master Spec Part 8."""

from __future__ import annotations

import logging
from typing import Any

from engines.platform_sdk.event_bus import get_platform_bus
from engines.platform_sdk.manager import PlatformPluginManager, get_plugin_manager
from engines.platform_sdk.public_api import PublicAPI, get_public_api
from engines.platform_sdk.security import assert_core_protected
from engines.platform_sdk.types import PLATFORM_SDK_VERSION, PlatformEvent

logger = logging.getLogger("tubedub.platform_sdk")


def bootstrap_platform(*, discover_builtin: bool = True) -> dict[str, Any]:
    """
    Initialize Platform SDK without modifying Core engines.
    """
    assert_core_protected()
    api = get_public_api()
    mgr = get_plugin_manager()
    discovered = mgr.discover_builtin() if discover_builtin else []
    get_platform_bus().publish(
        PlatformEvent.PIPELINE_FINISHED,
        {"bootstrap": True, "sdk": PLATFORM_SDK_VERSION},
    )
    logger.info(
        "PlatformSDK %s bootstrapped plugins=%d",
        PLATFORM_SDK_VERSION,
        len(discovered),
    )
    return {
        "version": PLATFORM_SDK_VERSION,
        "core_protected": True,
        "plugins_discovered": len(discovered),
        "extension_points": api.list_extension_points(),
        "settings_profiles": list(api.settings_profiles().keys()),
    }


def platform_status() -> dict[str, Any]:
    api = get_public_api()
    mgr = get_plugin_manager()
    return {
        "version": PLATFORM_SDK_VERSION,
        "core_protected": True,
        "plugins": mgr.list_plugins(),
        "extensions": api.list_extension_points(),
        "event_history": get_platform_bus().history(limit=20),
        "marketplace_kinds": api.marketplace().kinds(),
        "team_roles": [r.value for r in __import__("engines.platform_sdk.types", fromlist=["TeamRole"]).TeamRole],
    }
