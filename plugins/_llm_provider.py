"""LLM provider plugin factory helpers (OpenAI / Claude / Gemini / DeepSeek)."""

from __future__ import annotations

import os
from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_model, register_translation


def _env(*names: str) -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return ""


def make_llm_plugin(
    name: str,
    *,
    env_keys: tuple[str, ...],
    provider_hint: str,
) -> type[BasePlugin]:
    class _LLMPlugin(BasePlugin):
        PLUGIN_NAME = name
        PLUGIN_VERSION = "1.0.0"
        PLUGIN_CAPABILITIES = ["translation"]

        def on_init(self) -> None:
            register_translation(name, self.translate, plugin_name=name)
            register_model(name, {"provider": provider_hint, "name": name}, plugin_name=name)

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
            if not _env(*env_keys):
                # Still attempt dispatcher — may use local/configured routes
                pass
            prompt = (
                f"Translate from {src_lang} to {tgt_lang}. "
                f"Return only the translation.\n\n{text}"
            )
            try:
                from core.llm_dispatcher import get_dispatcher

                text_out, err, meta = get_dispatcher().execute_chat(
                    prompt,
                    task_type="translate",
                    model=model or name,
                    temperature=0.2,
                    source_lang=src_lang,
                    target_lang=tgt_lang,
                )
                if err or not (text_out or "").strip():
                    self.track_call(error=True)
                    return {
                        "ok": False,
                        "error": str(err or "empty_llm_output"),
                        "engine": name,
                        "configured": bool(_env(*env_keys)),
                        "meta": meta,
                    }
                return {
                    "ok": True,
                    "text": text_out.strip(),
                    "engine": name,
                    "configured": bool(_env(*env_keys)),
                    "meta": meta,
                }
            except Exception as exc:
                self.track_call(error=True)
                return {
                    "ok": False,
                    "error": str(exc),
                    "engine": name,
                    "configured": bool(_env(*env_keys)),
                }

    _LLMPlugin.__name__ = f"{name.title().replace('_', '')}Plugin"
    return _LLMPlugin
