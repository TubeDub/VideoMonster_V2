"""Fast Translation Engine — Marian / NLLB / OPUS via unified MT interface."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from engines.streamdub.base import ModuleCapabilities, StreamModule
from engines.streamdub.memory.translation_memory import TranslationMemory
from engines.streamdub.types import StreamSegment

logger = logging.getLogger("tubedub.streamdub.fast_mt")

_MT_BACKENDS = ("marian", "nllb", "opus", "argos")


class FastTranslationEngine(StreamModule):
    module_id = "fast_translation"

    def __init__(self) -> None:
        super().__init__()
        self._app_dir: Path | None = None
        self._backend = "marian"
        self._engines_loaded: set[str] = set()

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        self._app_dir = Path(app_dir) if app_dir else None
        cfg = config or {}
        self._backend = str(cfg.get("mt_backend") or "marian").lower()
        self._preload_backend(self._backend)

    def _preload_backend(self, backend: str) -> None:
        if backend in self._engines_loaded or not self._app_dir:
            return
        if backend == "marian":
            try:
                from engines.mt.stable_translate import ensure_marian_ready

                ensure_marian_ready(self._app_dir, "en", "uk")
                self._engines_loaded.add(backend)
            except Exception as exc:
                logger.debug("Marian preload skipped: %s", exc)
        else:
            self._engines_loaded.add(backend)

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        return True, f"backend={self._backend}", {"loaded": sorted(self._engines_loaded)}

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            backends=list(_MT_BACKENDS),
            features=["translation_memory", "batch_translate", "direct_pair"],
        )

    def _translate_one(
        self,
        text: str,
        src: str,
        tgt: str,
        backend: str,
    ) -> tuple[str, str]:
        if not text.strip() or src == tgt:
            return text, backend

        if backend == "marian" and self._app_dir:
            from engines.mt.stable_translate import translate_direct_marian

            out, _meta = translate_direct_marian(text, src, tgt, app_dir=self._app_dir)
            return str(out or text), "marian"

        if backend == "nllb":
            try:
                from engines.mt.nllb_engine import NLLBEngine

                eng = NLLBEngine()
                if eng.is_available() and eng.supports_pair(src, tgt):
                    res = eng.translate(text, src, tgt)
                    if res.text:
                        return res.text, "nllb"
            except Exception as exc:
                logger.debug("NLLB failed: %s", exc)

        if backend == "opus":
            try:
                from engines.mt.argos_engine import ArgosEngine

                eng = ArgosEngine()
                if eng.is_available():
                    res = eng.translate(text, src, tgt)
                    if res.text:
                        return res.text, "opus"
            except Exception as exc:
                logger.debug("OPUS/Argos failed: %s", exc)

        if self._app_dir:
            from engines.mt.stable_translate import translate_direct_marian

            out, _meta = translate_direct_marian(text, src, tgt, app_dir=self._app_dir)
            return str(out or text), "marian_fallback"

        return text, "none"

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        segments: list[StreamSegment] = list(payload.get("segments") or [])
        src = str(payload.get("source_lang") or "en")
        tgt = str(payload.get("target_lang") or "uk")
        backend = str(payload.get("mt_backend") or self._backend)
        project_id = str(payload.get("project_id") or "default")
        entity_mgr = payload.get("entity_manager")
        tm: TranslationMemory | None = payload.get("translation_memory")

        t0 = time.perf_counter()
        cache_hits = 0
        for seg in segments:
            cached = tm.lookup(seg.text, src, tgt, backend) if tm else None
            if cached:
                seg.translated = cached
                seg.route = "tm_hit"
                cache_hits += 1
                continue

            translated, used = self._translate_one(seg.text, src, tgt, backend)
            if entity_mgr is not None:
                translated = entity_mgr.apply(translated, original=seg.text)
            seg.translated = translated
            seg.route = used
            if tm:
                tm.store(seg.text, translated, src, tgt, used)

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "segments": segments,
            "backend": backend,
            "cache_hits": cache_hits,
            "elapsed_ms": elapsed_ms,
        }
