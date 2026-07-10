"""Online fallback engine (deep-translator / Google)."""

from __future__ import annotations

import logging
import time

from engines.mt.base import BaseMTEngine, MTResult
from engines.mt.lang_codes import deep_lang, normalize_lang

logger = logging.getLogger("tubedub.engines.mt.deep")


class DeepTranslatorEngine(BaseMTEngine):
    id = "deep"
    name = "deep-translator (Google)"
    version = "1.11"
    offline = False
    priority = 90

    def is_available(self) -> bool:
        try:
            from deep_translator import GoogleTranslator  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_pair(self, src_lang: str, tgt_lang: str) -> bool:
        return self.is_available() and normalize_lang(src_lang) != normalize_lang(tgt_lang)

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> MTResult:
        from deep_translator import GoogleTranslator

        src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
        t0 = time.perf_counter()
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return MTResult(text="", engine_id=self.id, offline=False)

        try:
            tr = GoogleTranslator(source=deep_lang(src), target=deep_lang(tgt))
            chunk = 4500
            if len(clean) <= chunk:
                result = tr.translate(clean)
            else:
                parts = [tr.translate(clean[i : i + chunk]) for i in range(0, len(clean), chunk)]
                result = " ".join(parts)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000.0
            return MTResult(text="", engine_id=self.id, error=str(e), offline=False, elapsed_ms=ms)

        ms = (time.perf_counter() - t0) * 1000.0
        return MTResult(text=str(result or "").strip(), engine_id=self.id, offline=False, elapsed_ms=ms)
