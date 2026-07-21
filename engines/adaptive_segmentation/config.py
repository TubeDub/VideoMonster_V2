"""Adaptive Segmentation 2.0 — tunable limits (TZ)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class AdaptiveSegConfig:
    """Dub-oriented segment length bounds (milliseconds)."""

    min_ms: int = 4500
    max_ms: int = 16000
    preferred_ms: int = 9000
    # Soft band for balance / “even” segments
    soft_min_ms: int = 5500
    soft_max_ms: int = 14000
    # Aggressiveness 0..1 — higher → more split/merge
    aggressiveness: float = 0.65
    use_meaning: bool = True
    use_tts_forecast: bool = True
    # EN→UK/RU spoken expansion heuristic for pre-MT forecast
    translation_expand: float = 1.18
    # Max duration variance ratio (max/min) before rebalance pass
    max_spread_ratio: float = 3.5
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(500, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_adaptive_seg_config(
    *,
    overrides: dict[str, Any] | None = None,
) -> AdaptiveSegConfig:
    cfg = AdaptiveSegConfig(
        min_ms=_env_int("VM_ADAPTIVE_SEG_MIN_MS", 4500),
        max_ms=_env_int("VM_ADAPTIVE_SEG_MAX_MS", 16000),
        preferred_ms=_env_int("VM_ADAPTIVE_SEG_PREFERRED_MS", 9000),
        aggressiveness=max(
            0.0, min(1.0, _env_float("VM_ADAPTIVE_SEG_AGGRESSIVENESS", 0.65))
        ),
        use_meaning=(os.getenv("VM_ADAPTIVE_SEG_MEANING") or "1").strip().lower()
        not in ("0", "false", "off", "no"),
        use_tts_forecast=(os.getenv("VM_ADAPTIVE_SEG_TTS_FORECAST") or "1")
        .strip()
        .lower()
        not in ("0", "false", "off", "no"),
        translation_expand=max(
            0.9, min(1.8, _env_float("VM_ADAPTIVE_SEG_EXPAND", 1.18))
        ),
        enabled=(os.getenv("VM_ADAPTIVE_SEG") or "1").strip().lower()
        not in ("0", "false", "off", "no"),
    )
    ov = overrides or {}
    for key in (
        "min_ms",
        "max_ms",
        "preferred_ms",
        "soft_min_ms",
        "soft_max_ms",
        "aggressiveness",
        "use_meaning",
        "use_tts_forecast",
        "translation_expand",
        "max_spread_ratio",
        "enabled",
    ):
        if key in ov and ov[key] is not None:
            setattr(cfg, key, ov[key])
    # Keep invariants
    cfg.min_ms = min(cfg.min_ms, cfg.preferred_ms)
    cfg.max_ms = max(cfg.max_ms, cfg.preferred_ms + 1000)
    cfg.soft_min_ms = max(cfg.min_ms, cfg.soft_min_ms)
    cfg.soft_max_ms = min(cfg.max_ms, max(cfg.soft_max_ms, cfg.preferred_ms))
    return cfg
