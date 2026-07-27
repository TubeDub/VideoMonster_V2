"""
TubeDub / VideoMonster Engine — Audio Mix Config

Single source of truth for the professional dub mixer:
  * per-track levels (dub / original-voice / background music+SFX)
  * intelligent voice ducking (only the original human voice is lowered while
    the dubbed line plays; music / SFX / ambience stay alive)
  * smooth fade-in / fade-out around the dubbed speech

The mixer is driven by an accompaniment (music+SFX) stem produced by
``engines/source_separation.py``.  When a stem is available the original human
voice can be ducked (or fully removed) independently of the music/effects, which
is what makes the result feel like professional cinema dubbing instead of TTS
laid over the original track.

This module is intentionally free of FFmpeg specifics — it only resolves *what*
the mix should be.  ``engines/dub_engine.py`` turns the resolved config into
concrete FFmpeg filter graphs.  Keeping the policy here avoids duplicating mixing
logic across the pipeline (auto-dub, studio re-export, dev preview).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Any

FEATURE_ID = "voice_ducking"

# ── Defaults (Task 9: every parameter has a sane default) ────────────────────
DEFAULT_DUB_VOLUME = 1.0            # dubbed (translated) speech — reference level
DEFAULT_ORIGINAL_VOICE_VOLUME = 0.0  # original human voice under the dub (0 = removed)
DEFAULT_BACKGROUND_VOLUME = 1.0     # music + SFX + ambience stem — kept alive
DEFAULT_DUCKING_DB = -12.0          # how much the ORIGINAL VOICE is ducked during dub
DEFAULT_FADE_MS = 250               # symmetric fade used for ducking transitions
DEFAULT_DUCK_ATTACK_MS = 40         # how fast ducking engages when dub starts
DEFAULT_DUCK_RELEASE_MS = 350       # how smoothly original returns after dub ends

# Guard rails
_MIN_FADE_MS, _MAX_FADE_MS = 20, 1500
_MIN_DUCK_DB, _MAX_DUCK_DB = -60.0, 0.0


def is_voice_ducking_enabled() -> bool:
    """Feature-flag gate.  Never raises — degrades to enabled if flags unavailable."""
    env = os.environ.get("FEATURE_VOICE_DUCKING")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        from engines.core.feature_flags import is_enabled

        return is_enabled(FEATURE_ID, developer_session=True)
    except Exception:
        return True


def _clampf(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class AudioMixConfig:
    """Resolved, ready-to-render mix policy for a single dub render."""

    # Per-track levels (linear gain, 0..2)
    dub_volume: float = DEFAULT_DUB_VOLUME
    original_voice_volume: float = DEFAULT_ORIGINAL_VOICE_VOLUME
    background_volume: float = DEFAULT_BACKGROUND_VOLUME

    # Intelligent ducking of the ORIGINAL VOICE only
    ducking_enabled: bool = True
    ducking_db: float = DEFAULT_DUCKING_DB
    fade_ms: int = DEFAULT_FADE_MS
    attack_ms: int = DEFAULT_DUCK_ATTACK_MS
    release_ms: int = DEFAULT_DUCK_RELEASE_MS

    def __post_init__(self) -> None:
        self.dub_volume = _clampf(float(self.dub_volume), 0.0, 2.0)
        self.original_voice_volume = _clampf(float(self.original_voice_volume), 0.0, 2.0)
        self.background_volume = _clampf(float(self.background_volume), 0.0, 2.0)
        self.ducking_db = _clampf(float(self.ducking_db), _MIN_DUCK_DB, _MAX_DUCK_DB)
        self.fade_ms = int(_clampf(float(self.fade_ms), _MIN_FADE_MS, _MAX_FADE_MS))
        self.attack_ms = int(_clampf(float(self.attack_ms), 1, _MAX_FADE_MS))
        self.release_ms = int(_clampf(float(self.release_ms), 1, _MAX_FADE_MS))

    # ── Derived FFmpeg-facing helpers ──────────────────────────────────────
    def ducked_gain(self) -> float:
        """Linear gain applied to the original voice while the dub is speaking."""
        return 10 ** (self.ducking_db / 20.0)

    def sidechain_params(self) -> dict[str, float]:
        """
        Map the human-facing ducking config onto FFmpeg ``sidechaincompress``
        parameters.  The dubbed line acts as the sidechain key: when it is loud
        the original voice is compressed down; when it is silent (between lines)
        the original voice returns to full — automatically, with smooth
        attack/release, so there are no abrupt jumps (TZ Task 6/8/10).
        """
        # Deeper ducking → higher ratio.  -12 dB target ≈ ratio ~8.
        ratio = _clampf(1.0 + abs(self.ducking_db) * 0.6, 2.0, 20.0)
        return {
            "threshold": 0.03,
            "ratio": ratio,
            "attack": float(self.attack_ms),
            "release": float(self.release_ms),
            "makeup": 1.0,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_mix_config(
    *,
    original_volume: float | None = None,
    dub_volume: float | None = None,
    background_volume: float | None = None,
    ducking_db: float | None = None,
    fade_ms: int | None = None,
    ducking_enabled: bool | None = None,
    content_mode_profile: Any | None = None,
    request: dict[str, Any] | None = None,
) -> AudioMixConfig:
    """
    Build an :class:`AudioMixConfig` from (in priority order):
      1. explicit keyword overrides (from the resolved dub style / API),
      2. a ``request`` dict (raw API body — keys ``ducking_db`` etc.),
      3. a content-mode profile (``engines/dubbing_engine/content_mode.py``),
      4. module defaults.

    This is the ONLY place mix defaults are decided, so every entry point
    (auto-dub, studio, preview) produces a consistent, professional mix.
    """
    req = request or {}

    def pick(explicit, req_key, prof_attr, default):
        if explicit is not None:
            return explicit
        if req_key in req and req[req_key] is not None:
            return req[req_key]
        if content_mode_profile is not None and prof_attr:
            val = getattr(content_mode_profile, prof_attr, None)
            if val is not None:
                return val
        return default

    cfg = AudioMixConfig(
        dub_volume=float(pick(dub_volume, "dub_volume", None, DEFAULT_DUB_VOLUME)),
        original_voice_volume=float(
            pick(original_volume, "original_volume", None, DEFAULT_ORIGINAL_VOICE_VOLUME)
        ),
        background_volume=float(
            pick(background_volume, "background_volume", None, DEFAULT_BACKGROUND_VOLUME)
        ),
        ducking_enabled=bool(
            pick(ducking_enabled, "ducking_enabled", "ducking_enabled", True)
        ),
        ducking_db=float(pick(ducking_db, "ducking_db", "ducking_level_db", DEFAULT_DUCKING_DB)),
        fade_ms=int(pick(fade_ms, "fade_ms", "ducking_fade_out_ms", DEFAULT_FADE_MS)),
    )
    return cfg
