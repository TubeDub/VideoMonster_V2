"""Integration hook for translate_text_traced."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.enterprise_translation.config import use_enterprise_translation
from engines.enterprise_translation.pipeline import translate_segment_enterprise


def translate_with_enterprise(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path,
    context: str | None = None,
    next_context: str | None = None,
    segment_index: int = -1,
) -> tuple[str, dict[str, Any]]:
    """Drop-in replacement for translation_manager in translate_text_traced."""
    translated, meta = translate_segment_enterprise(
        text,
        src_lang,
        tgt_lang,
        app_dir=app_dir,
        segment_index=segment_index,
    )
    meta.setdefault("context_used", bool(context and str(context).strip()))
    meta.setdefault("next_context_used", bool(next_context and str(next_context).strip()))
    meta.setdefault("translation_path", "enterprise")
    return translated, meta


__all__ = ["use_enterprise_translation", "translate_with_enterprise"]
