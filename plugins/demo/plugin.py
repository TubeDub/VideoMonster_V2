"""Demo plugin — SDK reference implementation (TZ #9 §10)."""

from __future__ import annotations

from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_translation


class Plugin(BasePlugin):
    PLUGIN_NAME = "demo"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["utility", "translation"]

    def on_init(self) -> None:
        register_translation(
            "demo_translate",
            self._translate,
            plugin_name=self.PLUGIN_NAME,
        )

    def _translate(self, text: str, **kwargs: Any) -> str:
        self.track_call()
        return f"[demo] {text}"


def create_plugin() -> Plugin:
    return Plugin()
