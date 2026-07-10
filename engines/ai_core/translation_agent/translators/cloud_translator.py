"""Cloud LLM translation — OpenAI wired; Claude/Gemini stub when keys present."""

from __future__ import annotations

import logging
import os

from engines.ai_core.translation_agent.translator_interface import BaseTranslator

logger = logging.getLogger("tubedub.translation_agent.cloud")

_LANG_NAMES = {
    "en": "English",
    "ru": "Russian",
    "uk": "Ukrainian",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pl": "Polish",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese",
}


def _has_openai_key() -> bool:
    return bool(
        os.getenv("OPENAI_API_KEY")
        or os.getenv("VM_OPENAI_API_KEY")
        or os.getenv("VM_LLM_API_KEY")
    )


def _has_anthropic_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("VM_ANTHROPIC_API_KEY"))


def _has_gemini_key() -> bool:
    return bool(
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("VM_GEMINI_API_KEY")
    )


class CloudTranslator(BaseTranslator):
    name = "cloud"

    def is_available(self) -> bool:
        if _has_openai_key():
            try:
                from engines.ai_core import llm_gateway

                return llm_gateway.is_available()
            except Exception:
                return True
        return _has_anthropic_key() or _has_gemini_key()

    def _provider(self) -> str:
        if _has_openai_key():
            return "openai"
        if _has_anthropic_key():
            return "anthropic"
        if _has_gemini_key():
            return "gemini"
        return "none"

    def translate(self, text: str, source: str, target: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""

        src_name = _LANG_NAMES.get(source, source)
        tgt_name = _LANG_NAMES.get(target, target)
        system = (
            "You are a professional subtitle translator. "
            "Translate faithfully without shortening, merging, or adding commentary. "
            "Preserve names, numbers, and dates exactly."
        )
        prompt = f"Translate from {src_name} to {tgt_name}:\n\n{clean}"

        provider = self._provider()
        if provider == "openai":
            from engines.ai_core import llm_gateway

            result = llm_gateway.chat(
                prompt,
                system=system,
                max_tokens=max(256, len(clean) * 3),
                temperature=0.1,
            )
            if result and str(result).strip():
                return str(result).strip()
            raise RuntimeError("OpenAI/LLM gateway returned empty translation")

        if provider == "anthropic":
            logger.warning("Anthropic API key set but CloudTranslator v1.0 uses OpenAI only")
            raise RuntimeError("anthropic_not_wired_v1")

        if provider == "gemini":
            logger.warning("Gemini API key set but CloudTranslator v1.0 uses OpenAI only")
            raise RuntimeError("gemini_not_wired_v1")

        raise RuntimeError("no_cloud_provider")
