"""Builtin Ollama translation/LLM plugin via LLMDispatcher."""

from __future__ import annotations

from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_model, register_translation


class Plugin(BasePlugin):
    PLUGIN_NAME = "ollama"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["translation"]

    def on_init(self) -> None:
        register_translation("ollama", self.translate, plugin_name=self.PLUGIN_NAME)
        register_model(
            "ollama",
            {"provider": "ollama", "name": "ollama"},
            plugin_name=self.PLUGIN_NAME,
        )

    def translate(
        self,
        text: str,
        *,
        src_lang: str = "en",
        tgt_lang: str = "ru",
        model: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.track_call()
        prompt = (
            f"Translate from {src_lang} to {tgt_lang}. "
            f"Return only the translation.\n\n{text}"
        )
        try:
            from core.llm_dispatcher import get_dispatcher

            text_out, err, meta = get_dispatcher().execute_chat(
                prompt,
                task_type="translate",
                model=model or None,
                temperature=0.2,
                source_lang=src_lang,
                target_lang=tgt_lang,
            )
            if err or not (text_out or "").strip():
                self.track_call(error=True)
                return {
                    "ok": False,
                    "error": str(err or "empty_llm_output"),
                    "engine": "ollama",
                    "meta": meta,
                }
            return {"ok": True, "text": text_out.strip(), "engine": "ollama", "meta": meta}
        except Exception as exc:
            self.track_call(error=True)
            return {"ok": False, "error": str(exc), "engine": "ollama"}


def create_plugin() -> Plugin:
    return Plugin()
