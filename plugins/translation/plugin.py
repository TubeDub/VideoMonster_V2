"""Builtin translation plugin — wraps engines.translation.translate_text."""

from __future__ import annotations

from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_translation


class Plugin(BasePlugin):
    PLUGIN_NAME = "translation"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["translation"]

    def on_init(self) -> None:
        register_translation("builtin_mt", self.translate, plugin_name=self.PLUGIN_NAME)

    def translate(
        self,
        text: str,
        *,
        src_lang: str = "en",
        tgt_lang: str = "ru",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.track_call()
        try:
            from engines.translation import translate_text_traced

            out, meta = translate_text_traced(text, src_lang, tgt_lang)
            return {"ok": True, "text": out, "meta": meta, "engine": "builtin_mt"}
        except Exception as exc:
            self.track_call(error=True)
            return {"ok": False, "error": str(exc), "engine": "builtin_mt"}


def create_plugin() -> Plugin:
    return Plugin()
