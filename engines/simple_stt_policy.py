# -*- coding: utf-8 -*-
"""Simple / Happy Path STT policy (Stage 8) — pyVideoTrans-style fast Whisper.

Default: faster-whisper small, beam=1, VAD on, word_timestamps off.
CUDA → float16; CPU → int8. Cap model at small (no medium/large in Simple).

Spec v3: opt-in `stt_quality` = simple | standard | high.
  high → large-v3 + word_timestamps (opt-in via VM_STT_QUALITY=high or
  task.info.stt_quality/spec_v3=True). Simple stays small by default.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("tubedub.simple_stt_policy")

SIMPLE_STT_DEFAULT_MODEL = "small"
SIMPLE_STT_MAX_MODEL = "small"
STANDARD_STT_MODEL = "medium"
HIGH_STT_MODEL = "large-v3"
_MODEL_RANK = ("tiny", "base", "small", "medium", "large", "large-v2", "large-v3")


def resolve_stt_quality(task_info: dict[str, Any] | None = None) -> str:
    """spec v3: return one of simple | standard | high (default simple)."""
    info = dict(task_info or {})
    q = str(info.get("stt_quality") or "").strip().lower()
    if q in ("simple", "standard", "high"):
        return q
    if info.get("spec_v3"):
        return "high"
    env = (os.getenv("VM_STT_QUALITY") or "").strip().lower()
    if env in ("simple", "standard", "high"):
        return env
    return "simple"


def resolve_stt_model_for_quality(
    quality: str,
    *,
    requested: str | None = None,
) -> str:
    """Map quality tier → model. Never downgrade below requested when it's larger."""
    q = str(quality or "simple").strip().lower()
    if q == "high":
        return HIGH_STT_MODEL
    if q == "standard":
        # Standard: user request wins if between small..large; else medium.
        req = str(requested or "").strip().lower()
        if req in ("medium", "large", "large-v2", "large-v3"):
            return req
        return STANDARD_STT_MODEL
    # simple → capped
    return resolve_simple_stt_model(requested)


def word_timestamps_for_quality(quality: str) -> bool:
    """Spec: high quality → word-level timestamps on."""
    return str(quality or "").lower() == "high"


def _env_model_override() -> str | None:
    raw = (os.getenv("VM_SIMPLE_STT_MODEL") or "").strip().lower()
    if raw in _MODEL_RANK or raw in ("tiny", "base", "small"):
        return raw
    return None


def resolve_simple_stt_model(requested: str | None = None) -> str:
    """Cap Simple Whisper size at small (or env override)."""
    override = _env_model_override()
    if override:
        return override
    req = str(requested or SIMPLE_STT_DEFAULT_MODEL).strip().lower() or SIMPLE_STT_DEFAULT_MODEL
    if req not in ("tiny", "base", "small", "medium", "large"):
        req = SIMPLE_STT_DEFAULT_MODEL
    # Faster than default is OK (tiny/base); slower (medium/large) is capped.
    try:
        if _MODEL_RANK.index(req) > _MODEL_RANK.index(SIMPLE_STT_MAX_MODEL):
            return SIMPLE_STT_DEFAULT_MODEL
    except ValueError:
        return SIMPLE_STT_DEFAULT_MODEL
    # If UI/API omitted size → small (not medium).
    if not requested:
        return SIMPLE_STT_DEFAULT_MODEL
    return req


def resolve_simple_stt_beam(*, model_size: str = "small") -> int:
    raw = (os.getenv("VM_SIMPLE_STT_BEAM") or "").strip()
    if raw.isdigit():
        return max(1, min(5, int(raw)))
    return 1


def resolve_simple_stt_device() -> tuple[str, str]:
    from engines.hardware_probe import probe_whisper_device

    return probe_whisper_device()


def should_force_simple_stt(task_info: dict[str, Any] | None = None) -> bool:
    """True only for Simple / Happy Path — Pro may keep medium/large + beam>1.

    Spec v3: `stt_quality` = standard/high or `spec_v3=True` opts out of Simple.
    """
    info = dict(task_info or {})
    if resolve_stt_quality(info) in ("standard", "high"):
        return False
    if info.get("simple_pipeline") or info.get("happy_path"):
        return True
    if info.get("simple_stt_locked"):
        return True
    try:
        from engines.happy_path import is_simple_mode

        return bool(is_simple_mode(info))
    except Exception:
        mode = str(info.get("user_mode") or "").strip().lower()
        return mode in ("basic", "simple", "")


def apply_simple_stt_policy(
    task_info: dict[str, Any],
    *,
    requested_model: str | None = None,
) -> dict[str, Any]:
    """Stamp STT knobs onto task info (idempotent). Simple by default,
    spec v3 high/standard when opted in."""
    quality = resolve_stt_quality(task_info)
    if quality in ("standard", "high"):
        model = resolve_stt_model_for_quality(
            quality,
            requested=(
                requested_model
                if requested_model is not None
                else task_info.get("model_size")
            ),
        )
        word_ts = word_timestamps_for_quality(quality)
        beam = 5 if quality == "high" else 3
        locked = False
    else:
        model = resolve_simple_stt_model(
            requested_model if requested_model is not None else task_info.get("model_size")
        )
        word_ts = False
        beam = resolve_simple_stt_beam(model_size=model)
        locked = True

    device, compute = resolve_simple_stt_device()
    task_info.update(
        {
            "model_size": model,
            "stt_model": model,
            "stt_engine": "faster-whisper",
            "stt_beam_size": beam,
            "stt_vad_filter": True,
            "stt_word_timestamps": word_ts,
            "stt_device": device,
            "stt_compute_type": compute,
            "stt_best_of": 1,
            "simple_stt_locked": locked,
            "voice_verification_asr_allowed": quality == "high",
            "post_tts_restt_allowed": quality == "high",
            "stt_quality": quality,
        }
    )
    logger.info(
        "stt_policy: quality=%s model=%s word_ts=%s device=%s compute=%s beam=%s",
        quality,
        model,
        word_ts,
        device,
        compute,
        beam,
    )
    return task_info


def empty_stt_stats() -> dict[str, Any]:
    return {
        "stt_wall_sec": 0.0,
        "stt_model": "",
        "stt_device": "",
        "stt_compute_type": "",
        "stt_beam_size": 1,
        "stt_vad_filter": True,
        "stt_engine": "faster-whisper",
        "stt_segments_raw": 0,
        "stt_segments_after_glue": 0,
        "stt_cache_hit": False,
        "stt_word_timestamps": False,
    }
