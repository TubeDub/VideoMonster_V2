"""
Single-line integration hook for TubeDub pipeline.

Usage (one line after Naturalizer, before TTS):

    segments = __import__('engines.language_intelligence.integration', fromlist=['apply']).apply(segments, meta, src_lang=src, tgt_lang=tgt, task_id=task_id)

Or:

    from engines.language_intelligence.integration import apply_before_tts
    segments = apply_before_tts(segments, segment_meta, ...)

Safe no-op when disabled or module missing. Does not import if VM_LANGUAGE_INTELLIGENCE=0.
"""

from __future__ import annotations

import os
from typing import Any


def _enabled() -> bool:
    return (os.getenv("VM_LANGUAGE_INTELLIGENCE") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def apply_before_tts(
    segments: list[str],
    segment_meta: list[dict[str, Any]],
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    task_id: str = "",
    app_dir=None,
) -> list[str]:
    """
    Replace segment texts with Language Intelligence output when enabled.
    segment_meta[i]: {original, raw_mt, naturalized, final}
    Returns original segments unchanged if disabled.
    """
    if not _enabled() or not segments:
        return segments

    try:
        from pathlib import Path

        from engines.language_intelligence.pipeline import process_segments

        items: list[dict[str, Any]] = []
        for i, text in enumerate(segments):
            m = segment_meta[i] if i < len(segment_meta) else {}
            items.append(
                {
                    "original": m.get("original") or m.get("whisper_text") or "",
                    "raw_mt": m.get("raw_mt") or m.get("raw_translation") or "",
                    "naturalized": m.get("naturalized") or m.get("naturalized_text") or "",
                    "final": m.get("final") or m.get("final_text") or text,
                }
            )
        improved, _ = process_segments(
            items,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            task_id=task_id,
            app_dir=Path(app_dir) if app_dir else None,
        )
        if len(improved) == len(segments):
            return improved
    except Exception:
        pass
    return segments


def apply(
    segments: list[str],
    segment_meta: list[dict[str, Any]],
    **kwargs: Any,
) -> list[str]:
    """Short alias for one-line hook."""
    return apply_before_tts(segments, segment_meta, **kwargs)
