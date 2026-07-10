"""Argos Translate wrapper — delegates to engines/translation.py."""

from __future__ import annotations

import logging

from engines.ai_core.translation_agent.translator_interface import BaseTranslator

logger = logging.getLogger("tubedub.translation_agent.argos")


class ArgosTranslator(BaseTranslator):
    name = "argos"

    def is_available(self) -> bool:
        try:
            from engines.mt.argos_engine import ArgosEngine

            return ArgosEngine().is_available()
        except Exception:
            return False

    def translate(self, text: str, source: str, target: str) -> str:
        from engines.translation import translate_text

        clean = str(text or "").strip()
        if not clean:
            return ""
        try:
            return translate_text(clean, source, target)
        except Exception as exc:
            logger.warning("Argos translate failed: %s", exc)
            from engines.mt.argos_engine import translate_argos

            result = translate_argos(clean, source, target)
            if result:
                return result
            raise
