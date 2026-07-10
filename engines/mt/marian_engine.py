"""MarianMT (Helsinki-NLP) offline engine via transformers."""

from __future__ import annotations

import logging
import time

from engines.mt.base import BaseMTEngine, MTResult
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.engines.mt.marian")

_MODEL_CACHE: dict[str, tuple | None] = {}


def _marian_model_name(src: str, tgt: str) -> str:
    return f"Helsinki-NLP/opus-mt-{src}-{tgt}"


def _load_marian(src: str, tgt: str, app_dir=None):
    key = f"{src}->{tgt}"
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        from pathlib import Path

        from engines.model_manager.downloader import load_marian

        if app_dir is None:
            app_dir = Path(__file__).resolve().parent.parent.parent

        loaded = load_marian(app_dir, src, tgt)
        _MODEL_CACHE[key] = loaded
        return loaded
    except Exception as e:
        logger.debug("[MT/Marian] load %s→%s: %s", src, tgt, e)
        _MODEL_CACHE[key] = None
        return None


class MarianEngine(BaseMTEngine):
    id = "marian"
    name = "MarianMT (Helsinki-NLP)"
    version = "opus-mt"
    offline = True
    priority = 20

    def is_available(self) -> bool:
        try:
            import transformers  # noqa: F401
            import torch  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_pair(self, src_lang: str, tgt_lang: str) -> bool:
        src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
        if src == tgt:
            return True
        if not self.is_available():
            return False
        key = f"{src}->{tgt}"
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key] is not None
        name = _marian_model_name(src, tgt)
        try:
            from pathlib import Path

            from engines.model_manager.integrity import verify_hf_model as model_is_local
            from engines.model_manager.runtime import is_offline_only

            app_dir = Path(__file__).resolve().parent.parent.parent
            if model_is_local(app_dir, name):
                return True
            if is_offline_only():
                return False
            return True
        except Exception:
            return False

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> MTResult:
        import torch

        src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
        t0 = time.perf_counter()
        if src == tgt:
            return MTResult(text=str(text or ""), engine_id=self.id, offline=True)

        loaded = _load_marian(src, tgt)
        if not loaded:
            return MTResult(text="", engine_id=self.id, error="no_model", offline=True)
        tok, model, name = loaded
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return MTResult(text="", engine_id=self.id, offline=True)

        try:
            batch = tok([clean], return_tensors="pt", padding=True, truncation=True, max_length=512)
            num_beams = 1
            with torch.no_grad():
                out = model.generate(**batch, max_length=512, num_beams=num_beams)
            result = tok.decode(out[0], skip_special_tokens=True).strip()
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000.0
            return MTResult(text="", engine_id=self.id, error=str(e), offline=True, elapsed_ms=ms)

        ms = (time.perf_counter() - t0) * 1000.0
        if not result:
            return MTResult(text="", engine_id=self.id, error="empty", offline=True, elapsed_ms=ms)
        return MTResult(
            text=result,
            engine_id=self.id,
            engine_version=name,
            offline=True,
            elapsed_ms=ms,
        )

    def estimate_memory_mb(self) -> int:
        return 350
