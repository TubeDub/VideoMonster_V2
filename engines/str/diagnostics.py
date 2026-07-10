"""Self-diagnostics — detect degrading or improving engines."""

from __future__ import annotations

from pathlib import Path

from engines.str.config import DEGRADATION_DROP, TREND_WINDOW
from engines.str.knowledge_base import engine_stats


def engine_trend(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
    engine_id: str,
) -> dict[str, float | str | bool]:
    """
    Compare recent vs older quality scores.
    Returns trend direction and delta.
    """
    stats = engine_stats(app_dir, src_lang, tgt_lang, engine_id)
    recent: list[float] = stats.get("recent_scores") or []
    if len(recent) < 4:
        return {"delta": 0.0, "direction": "unknown", "degrading": False, "improving": False}

    half = max(2, len(recent) // 2)
    old_avg = sum(recent[:half]) / half
    new_avg = sum(recent[half:]) / max(len(recent) - half, 1)
    delta = round(new_avg - old_avg, 2)

    degrading = delta <= -DEGRADATION_DROP
    improving = delta >= DEGRADATION_DROP
    direction = "stable"
    if degrading:
        direction = "degrading"
    elif improving:
        direction = "improving"

    return {
        "delta": delta,
        "direction": direction,
        "degrading": degrading,
        "improving": improving,
        "old_avg": round(old_avg, 1),
        "new_avg": round(new_avg, 1),
        "window": min(len(recent), TREND_WINDOW),
    }


def priority_adjustment(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
    engine_id: str,
) -> float:
    """
    Bonus/penalty applied to ranking score based on trend.
    Negative = lower priority, positive = raise.
    """
    trend = engine_trend(app_dir, src_lang, tgt_lang, engine_id)
    if trend.get("degrading"):
        return -12.0
    if trend.get("improving"):
        return 8.0
    return 0.0
