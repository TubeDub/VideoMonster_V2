"""MT bridge backend — wraps existing MT without coupling Translation Core to models."""

from __future__ import annotations

import logging
from typing import Any

from engines.translation_core.backend import BackendCapabilities, TranslationBackend

logger = logging.getLogger("tubedub.translation_core.mt_bridge")


class MTBridgeBackend(TranslationBackend):
    id = "mt_bridge"
    name = "MT Bridge"
    version = "1"
    _ready = False

    def initialize(self) -> None:
        self._ready = True

    def translate(
        self,
        text: str,
        *,
        src_lang: str,
        tgt_lang: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        src = str(text or "")
        if not src.strip():
            return src
        try:
            from engines.translation import translate_text

            return str(translate_text(src, src_lang=src_lang, tgt_lang=tgt_lang) or src)
        except Exception as exc:
            logger.debug("mt_bridge translate_text failed: %s", exc)
        try:
            from engines.translation_compat import translate_text as compat

            return str(compat(src, target=tgt_lang, source=src_lang) or src)
        except Exception:
            pass
        # Offline fallback: heuristic
        from engines.translation_core.backends.heuristic import HeuristicBackend

        return HeuristicBackend().translate(
            src, src_lang=src_lang, tgt_lang=tgt_lang, context=context
        )

    def health_check(self) -> bool:
        return True

    def shutdown(self) -> None:
        self._ready = False

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            offline=False,
            multi_variant=False,
            context_aware=True,
            languages=["en", "uk", "ru", "de", "fr", "es"],
        )
