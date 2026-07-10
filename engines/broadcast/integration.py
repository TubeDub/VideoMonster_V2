"""Broadcast pipeline integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.broadcast.config import use_broadcast_pipeline
from engines.broadcast.pipeline import translate_segment_broadcast


def translate_with_broadcast(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path,
    context: str | None = None,
    next_context: str | None = None,
    segment_index: int = -1,
    source_original: str | None = None,
) -> tuple[str, dict[str, Any]]:
    translated, meta = translate_segment_broadcast(
        text,
        src_lang,
        tgt_lang,
        app_dir=app_dir,
        segment_index=segment_index,
    )
    meta.setdefault("context_used", bool(context and str(context).strip()))
    meta.setdefault("next_context_used", bool(next_context and str(next_context).strip()))
    return translated, meta


__all__ = ["use_broadcast_pipeline", "translate_with_broadcast"]
