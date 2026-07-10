"""TubeDub unified platform architecture."""

from engines.tubedub.lifecycle import (
    HealthReport,
    ModuleLifecycleState,
    PlatformModule,
)
from engines.tubedub.release import ReleaseChannel
from engines.tubedub.api_bus import ApiBus, get_api_bus
from engines.tubedub.plugin_host import PluginHost, get_plugin_host
from engines.tubedub.module_manager import PlatformModuleManager, get_module_manager
from engines.tubedub.bootstrap import bootstrap_platform

__all__ = [
    "HealthReport",
    "ModuleLifecycleState",
    "PlatformModule",
    "ReleaseChannel",
    "ApiBus",
    "get_api_bus",
    "PluginHost",
    "get_plugin_host",
    "PlatformModuleManager",
    "get_module_manager",
    "bootstrap_platform",
]
