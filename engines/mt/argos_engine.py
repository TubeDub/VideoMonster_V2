"""Argos Translate engine adapter."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from engines.mt.base import BaseMTEngine, MTResult
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.engines.mt.argos")

_MODEL_CACHE: dict[str, object | None] = {}
_APP_DIR = Path(__file__).resolve().parent.parent.parent


def _get_translator(src: str, tgt: str, app_dir=None):
    key = f"{src}->{tgt}"
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        import argostranslate.translate as argos

        installed = argos.get_installed_languages()
        fl = next((l for l in installed if l.code == src), None)
        tl = next((l for l in installed if l.code == tgt), None)
        if fl and tl:
            tr = fl.get_translation(tl)
            _MODEL_CACHE[key] = tr
            return tr

        from engines.model_manager.downloader import load_argos_translator

        if app_dir is None:
            app_dir = _APP_DIR
        tr = load_argos_translator(app_dir, src, tgt)
        _MODEL_CACHE[key] = tr
        return tr
    except ImportError:
        logger.debug("[MT/Argos] not installed")
    except Exception as e:
        logger.warning("[MT/Argos] error: %s", e)
    _MODEL_CACHE[key] = None
    return None


class ArgosEngine(BaseMTEngine):
    id = "argos"
    name = "Argos Translate"
    version = "1.9"
    offline = True
    priority = 40

    def is_available(self) -> bool:
        try:
            import argostranslate.translate  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_pair(self, src_lang: str, tgt_lang: str) -> bool:
        src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
        if src == tgt:
            return True
        if not self.is_available():
            return False
        from engines.model_manager.downloader import _argos_ready, argos_pair_available

        if _argos_ready(src, tgt):
            return True
        return argos_pair_available(src, tgt)

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> MTResult:
        src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
        t0 = time.perf_counter()
        if src == tgt:
            return MTResult(text=str(text or ""), engine_id=self.id, offline=True, elapsed_ms=0)

        translator = _get_translator(src, tgt)
        if not translator:
            return MTResult(text="", engine_id=self.id, error="no_model", offline=True)

        clean = " ".join(str(text or "").split())
        from engines.mt.sentence_split import split_mt_sentences

        parts = split_mt_sentences(clean)
        # Long paragraphs: always sentence-chunk (Argos truncates mega-blobs)
        if len(clean.split()) >= 40 and len(parts) == 1:
            # Soft-split on commas/semicolons for runaway ASR blobs
            soft = re.split(r"(?<=[,;:])\s+", clean)
            parts = [p.strip() for p in soft if p.strip()] or parts
        out: list[str] = []
        for s in parts:
            if not s.strip():
                continue
            try:
                out.append(translator.translate(s))
            except Exception as e:
                logger.warning("[MT/Argos] sentence failed: %s", e)
                out.append(s)
        result = " ".join(out).strip()
        ms = (time.perf_counter() - t0) * 1000.0
        if not result or result == clean:
            return MTResult(text="", engine_id=self.id, error="unchanged", offline=True, elapsed_ms=ms)
        return MTResult(text=result, engine_id=self.id, engine_version=self.version, offline=True, elapsed_ms=ms)


def translate_argos(text: str, src: str, tgt: str) -> str | None:
    r = ArgosEngine().translate(text, src, tgt)
    return r.text or None
