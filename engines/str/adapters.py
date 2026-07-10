"""Wrap existing MT engines into the unified STR interface."""

from __future__ import annotations

import logging
from typing import Any

from engines.mt.base import BaseMTEngine, MTResult
from engines.str.types import STRTranslationResult

logger = logging.getLogger("tubedub.engines.str.adapters")


def mt_result_to_str(
    result: MTResult,
    *,
    src_lang: str,
    tgt_lang: str,
    quality_probability: float = 0.0,
    warnings: list[str] | None = None,
) -> STRTranslationResult:
    return STRTranslationResult(
        text=str(result.text or ""),
        engine_id=result.engine_id,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        elapsed_ms=result.elapsed_ms,
        warnings=list(warnings or []),
        quality_probability=quality_probability,
        error=str(result.error or ""),
        offline=result.offline,
        engine_version=result.engine_version,
        meta=dict(result.meta or {}),
    )


def translate_via_adapter(
    engine: BaseMTEngine,
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    quality_probability: float = 0.0,
) -> STRTranslationResult:
    try:
        result = engine.translate(text, src_lang, tgt_lang)
    except Exception as exc:
        logger.warning("[STR] %s translate failed: %s", engine.id, exc)
        return STRTranslationResult(
            text="",
            engine_id=engine.id,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            error=str(exc),
            offline=engine.offline,
            engine_version=getattr(engine, "version", ""),
        )
    return mt_result_to_str(
        result,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        quality_probability=quality_probability,
    )


def list_available_engines() -> list[BaseMTEngine]:
    from engines.mt.registry import get_registry

    return get_registry()
