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
            chunks = _chunk_for_mt(clean, src)
            if len(chunks) == 1:
                result = tr.translate(chunks[0])
            else:
                parts = []
                for ch in chunks:
                    try:
                        parts.append(tr.translate(ch))
                    except Exception as e:
                        logger.warning("[MT/deep] chunk failed: %s", e)
                result = " ".join(p for p in parts if p)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000.0
            return MTResult(text="", engine_id=self.id, error=str(e), offline=False, elapsed_ms=ms)

        ms = (time.perf_counter() - t0) * 1000.0
        return MTResult(text=str(result or "").strip(), engine_id=self.id, offline=False, elapsed_ms=ms)


def _chunk_for_mt(text: str, src: str) -> list[str]:
    """Split long CJK/ASR blobs so Google does not truncate mid-meaning."""
    import re

    clean = str(text or "").strip()
    if not clean:
        return []
    # Byte/char soft limit well under Google's ~5k
    limit = 180 if src in ("zh", "ja", "ko") else 4500
    if len(clean) <= limit:
        return [clean]
    if src in ("zh", "ja", "ko"):
        # Prefer CJK punctuation / spaces
        parts = re.split(r"(?<=[。！？；，、\s])", clean)
        parts = [p for p in parts if p and p.strip()]
        if len(parts) <= 1:
            # Fixed-width windows
            return [clean[i : i + limit] for i in range(0, len(clean), limit)]
        out: list[str] = []
        buf = ""
        for p in parts:
            if len(buf) + len(p) <= limit:
                buf += p
            else:
                if buf.strip():
                    out.append(buf.strip())
                buf = p
        if buf.strip():
            out.append(buf.strip())
        return out or [clean]
    return [clean[i : i + limit] for i in range(0, len(clean), limit)]
