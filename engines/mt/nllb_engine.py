"""Meta NLLB-200 distilled offline engine (multilingual)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from engines.mt.base import BaseMTEngine, MTResult
from engines.mt.lang_codes import nllb_code, normalize_lang

logger = logging.getLogger("tubedub.engines.mt.nllb")

_PIPELINE = None
_MODEL_ID = "facebook/nllb-200-distilled-600M"
_APP_DIR = Path(__file__).resolve().parent.parent.parent


def _get_pipeline(app_dir=None):
    global _PIPELINE
    if _PIPELINE is not None:
        return _PIPELINE
    try:
        from engines.model_manager.downloader import load_nllb

        if app_dir is None:
            app_dir = _APP_DIR
        _PIPELINE = load_nllb(app_dir)
        return _PIPELINE
    except Exception as e:
        logger.debug("[MT/NLLB] load failed: %s", e)
        return None


class NLLBEngine(BaseMTEngine):
    id = "nllb"
    name = "Meta NLLB-200"
    version = "distilled-600M"
    offline = True
    priority = 30

    def is_available(self) -> bool:
        try:
            import transformers  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_pair(self, src_lang: str, tgt_lang: str) -> bool:
        src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
        if src == tgt:
            return True
        return bool(nllb_code(src) and nllb_code(tgt) and self.is_available())

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> MTResult:
        src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
        t0 = time.perf_counter()
        if src == tgt:
            return MTResult(text=str(text or ""), engine_id=self.id, offline=True)

        src_nllb, tgt_nllb = nllb_code(src), nllb_code(tgt)
        if not src_nllb or not tgt_nllb:
            return MTResult(text="", engine_id=self.id, error="unsupported_lang", offline=True)

        pipe = _get_pipeline()
        if not pipe:
            return MTResult(text="", engine_id=self.id, error="not_loaded", offline=True)

        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return MTResult(text="", engine_id=self.id, offline=True)

        try:
            out = pipe(clean, src_lang=src_nllb, tgt_lang=tgt_nllb)
            result = str(out[0]["translation_text"]).strip() if out else ""
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000.0
            return MTResult(text="", engine_id=self.id, error=str(e), offline=True, elapsed_ms=ms)

        ms = (time.perf_counter() - t0) * 1000.0
        return MTResult(
            text=result,
            engine_id=self.id,
            engine_version=self.version,
            offline=True,
            elapsed_ms=ms,
        )

    def estimate_memory_mb(self) -> int:
        return 1200
