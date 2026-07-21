"""Argos Translate wrapper — calls ArgosEngine directly (sentence-safe)."""

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
        clean = str(text or "").strip()
        if not clean:
            return ""
        try:
            from engines.mt.argos_engine import ArgosEngine

            result = ArgosEngine().translate(clean, source, target)
            out = str(result.text or "").strip()
            if out:
                return out
            if result.error:
                logger.warning("ArgosEngine empty: %s", result.error)
        except Exception as exc:
            logger.warning("ArgosEngine failed: %s", exc)

        # Fallback: legacy translate_text path
        try:
            from engines.translation import translate_text

            return str(translate_text(clean, source, target) or "").strip()
        except Exception as exc:
            logger.warning("Argos legacy translate failed: %s", exc)
            from engines.mt.argos_engine import translate_argos

            return str(translate_argos(clean, source, target) or "").strip()
