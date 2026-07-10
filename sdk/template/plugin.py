"""My VideoMonster plugin — copy this template to plugins/my_plugin/."""

from __future__ import annotations

from typing import Any

from sdk.base import BasePlugin


class Plugin(BasePlugin):
    PLUGIN_NAME = "my_plugin"
    PLUGIN_VERSION = "0.1.0"
    PLUGIN_CAPABILITIES = ["utility"]

    def on_init(self) -> None:
        app_dir = self._context.get("app_dir", "")
        # Register handlers via SDK:
        # from sdk.core_api import register_translation
        # register_translation("my_plugin", self.process, plugin_name=self.PLUGIN_NAME)

    def process(self, data: Any, **kwargs: Any) -> Any:
        self.track_call()
        return data


def create_plugin() -> Plugin:
    return Plugin()
