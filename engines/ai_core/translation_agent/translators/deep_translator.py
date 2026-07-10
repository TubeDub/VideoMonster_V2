"""deep-translator (Google) fallback wrapper."""

from __future__ import annotations

import logging

from engines.ai_core.translation_agent.translator_interface import BaseTranslator

logger = logging.getLogger("tubedub.translation_agent.deep")


class DeepTranslatorWrapper(BaseTranslator):
    name = "deep-translator"

    def is_available(self) -> bool:
        try:
            from engines.mt.deep_engine import DeepTranslatorEngine

            return DeepTranslatorEngine().is_available()
        except Exception:
            return False

    def translate(self, text: str, source: str, target: str) -> str:
        from engines.translation import _translate_deep

        clean = str(text or "").strip()
        if not clean:
            return ""
        try:
            return _translate_deep(clean, source, target)
        except Exception as exc:
            logger.warning("deep-translator via translation.py failed: %s", exc)
        from engines.mt.deep_engine import DeepTranslatorEngine

        result = DeepTranslatorEngine().translate(clean, source, target)
        if not result.text:
            raise RuntimeError(result.error or "deep-translator failed")
        return result.text
