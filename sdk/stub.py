"""Factory for manifest-only builtin plugin stubs."""

from __future__ import annotations

from typing import Type

from sdk.base import BasePlugin


def stub_plugin(
    name: str,
    capabilities: list[str],
    *,
    version: str = "1.0.0",
    description: str = "",
) -> Type[BasePlugin]:
    """Create a minimal plugin class for a builtin capability provider."""

    class _Stub(BasePlugin):
        PLUGIN_NAME = name
        PLUGIN_VERSION = version
        PLUGIN_CAPABILITIES = list(capabilities)

        def on_init(self) -> None:
            self._desc = description or f"Builtin {name} provider"

    _Stub.__name__ = f"{name.title().replace('_', '')}Plugin"
    return _Stub
