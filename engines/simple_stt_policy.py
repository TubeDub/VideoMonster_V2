# -*- coding: utf-8 -*-
"""Simple / Happy Path STT policy (Stage 8) — pyVideoTrans-style fast Whisper.

Default: faster-whisper small, beam=1, VAD on, word_timestamps off.
CUDA → float16; CPU → int8. Cap model at small (no medium/large in Simple).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("tubedub.simple_stt_policy")

SIMPLE_STT_DEFAULT_MODEL = "small"
SIMPLE_STT_MAX_MODEL = "small"
_MODEL_RANK = ("tiny", "base", "small", "medium", "large", "large-v2", "large-v3")


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
    """True only for Simple / Happy Path — Pro may keep medium/large + beam>1."""
    info = dict(task_info or {})
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
    """Stamp Simple STT knobs onto task info (idempotent)."""
    model = resolve_simple_stt_model(
        requested_model if requested_model is not None else task_info.get("model_size")
    )
    device, compute = resolve_simple_stt_device()
    beam = resolve_simple_stt_beam(model_size=model)
    task_info.update(
        {
            "model_size": model,
            "stt_model": model,
            "stt_engine": "faster-whisper",
            "stt_beam_size": beam,
            "stt_vad_filter": True,
            "stt_word_timestamps": False,
            "stt_device": device,
            "stt_compute_type": compute,
            "stt_best_of": 1,
            "simple_stt_locked": True,
            "voice_verification_asr_allowed": False,
            "post_tts_restt_allowed": False,
        }
    )
    logger.info(
        "simple_stt: model=%s device=%s compute=%s beam=%s",
        model,
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
