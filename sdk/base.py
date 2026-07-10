"""SDK base classes for VideoMonster V2 plugin developers (TZ #9 §10)."""

from __future__ import annotations

import time
from typing import Any

from core.plugin_api import ExecutionMode, PluginHealth, VMPlugin


class BasePlugin(VMPlugin):
    """Convenience base class — override class attributes or methods."""

    PLUGIN_NAME: str = "base"
    PLUGIN_VERSION: str = "0.1.0"
    PLUGIN_CAPABILITIES: list[str] = []
    PLUGIN_DEPENDENCIES: list[str] = []
    EXECUTION_MODE: str = ExecutionMode.LOCAL.value
    REMOTE_ENDPOINT: str = ""

    _initialized: bool = False
    _context: dict[str, Any]

    def __init__(self) -> None:
        self._context = {}
        self._call_count = 0
        self._error_count = 0

    def initialize(self, context: dict[str, Any]) -> None:
        self._context = dict(context)
        self._initialized = True
        self.on_init()

    def shutdown(self) -> None:
        self.on_shutdown()
        self._initialized = False

    def health(self) -> PluginHealth:
        return PluginHealth(
            ok=self._initialized,
            message="ok" if self._initialized else "not initialized",
            details={"calls": self._call_count, "errors": self._error_count},
        )

    def capabilities(self) -> list[str]:
        return list(self.PLUGIN_CAPABILITIES)

    def version(self) -> str:
        return self.PLUGIN_VERSION

    def dependencies(self) -> list[str]:
        return list(self.PLUGIN_DEPENDENCIES)

    def execution_mode(self) -> str:
        return self.EXECUTION_MODE

    def remote_endpoint(self) -> str:
        return self.REMOTE_ENDPOINT

    def on_init(self) -> None:
        """Override for custom initialization."""

    def on_shutdown(self) -> None:
        """Override for custom cleanup."""

    def track_call(self, *, error: bool = False) -> None:
        self._call_count += 1
        if error:
            self._error_count += 1
