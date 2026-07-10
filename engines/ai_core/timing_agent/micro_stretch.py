"""±5% atempo ONLY as last resort — wraps timing_fit (max 1.05 / min 0.95)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.timing_agent.micro_stretch")

MICRO_STRETCH_MAX = 1.05
MICRO_STRETCH_MIN = 0.95


def apply_micro_stretch(
    audio_path: str | Path,
    slot_ms: int,
    work_dir: Path,
    *,
    tolerance_ms: int = 75,
) -> tuple[str | None, dict[str, Any]]:
    """
    Last-resort audio stretch within ±5%.
    Wraps engines.timing_fit.prepare_dub_segment_audio.
    """
    from engines.timing_fit import prepare_dub_segment_audio

    src = Path(audio_path)
    if not src.is_file() or slot_ms <= 0:
        return None, {"error": "invalid_input"}

    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        prepared, meta = prepare_dub_segment_audio(
            src,
            slot_ms,
            work_dir,
            max_atempo=MICRO_STRETCH_MAX,
            tolerance_ms=tolerance_ms,
        )
        atempo = float(meta.get("atempo") or 1.0)
        if atempo > MICRO_STRETCH_MAX:
            atempo = MICRO_STRETCH_MAX
        if atempo < MICRO_STRETCH_MIN:
            atempo = MICRO_STRETCH_MIN
        meta["atempo"] = atempo
        meta["micro_stretch"] = True
        meta["policy"] = f"±5% max={MICRO_STRETCH_MAX} min={MICRO_STRETCH_MIN}"
        return prepared, meta
    except Exception as exc:
        logger.debug("micro_stretch failed: %s", exc)
        return None, {"error": str(exc)}


def should_apply_micro_stretch(
    predicted_ms: int,
    slot_ms: int,
    *,
    text_attempts_exhausted: bool = True,
    tolerance_ms: int = 75,
) -> bool:
    """True only when text adaptation failed and overflow remains."""
    if not text_attempts_exhausted:
        return False
    if slot_ms <= 0:
        return False
    overflow = predicted_ms - slot_ms
    if overflow <= tolerance_ms:
        return False
    ratio = predicted_ms / max(slot_ms, 1)
    return 1.0 < ratio <= MICRO_STRETCH_MAX
