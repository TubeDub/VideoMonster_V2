# -*- coding: utf-8 -*-
"""Canonical Simple dub pipeline (pyVideoTrans / VideoLingo style).

One short path for ordinary users:

  video → FFmpeg audio → STT (4–8s glue) → 1:1 translate → text-fit
       → Edge-TTS → atempo 0.95–1.08 → FFmpeg mux → MP4

Advanced modules stay in the repo but are OFF here. Pro/Studio are untouched.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.simple_dub_pipeline")

# Explicit step list — documentation + acceptance checklist.
SIMPLE_PIPELINE_STEPS: tuple[str, ...] = (
    "extract_audio_ffmpeg",
    "stt_faster_whisper",
    "merge_segments_4_5_to_8s",
    "translate_one_to_one",
    "text_fit_to_slot",
    "tts_edge",
    "atempo_0_95_1_08",
    "mux_ffmpeg_mp4",
)

# Modules that must stay OFF in Simple (names for logs / reports).
SIMPLE_DISABLED: tuple[str, ...] = (
    "ada",
    "sso",
    "meaning_fit",
    "timing_aware_llm",
    "post_tts_resegment",
    "blind_timing_align",
    "long_silence_pad",
    "atempo_above_1_08",
    "tps_orchestrator",
    "adaptive_segmentation",
    "enterprise_cloud",
    "lip_sync",
)


def apply_simple_pipeline_policy(
    task_info: dict[str, Any],
    *,
    user_mode: str | None = None,
) -> dict[str, Any]:
    """Stamp all Simple/Happy Path gates onto task info (idempotent)."""
    from engines.happy_path import (
        HAPPY_PATH_HARD_MAX_ATEMPO_UK,
        HAPPY_PATH_MAX_ATEMPO,
        HAPPY_PATH_MAX_ATEMPO_UK,
        HAPPY_PATH_MIN_ATEMPO,
        HAPPY_PATH_NO_SPEECH_TRIM,
        is_simple_mode,
        stamp_happy_path_meta,
    )

    stamp_happy_path_meta(task_info, user_mode=user_mode)
    simple = is_simple_mode(task_info, user_mode=user_mode)
    if not simple and not task_info.get("happy_path"):
        return task_info

    # Force Happy Path knobs even when Pro left happy_path via env=0 elsewhere.
    if simple:
        task_info["user_mode"] = task_info.get("user_mode") or "basic"
        task_info["happy_path"] = True
        task_info["USE_ADVANCED_ADAPTATION"] = False
        task_info["adaptation_path"] = "happy_path"
        task_info["adaptation_shorteners"] = ["naturalizer", "text_slot_fit"]

    # Stage 28 §D1/F — UK Simple caps atempo at 1.05 (hard 1.08) and prefers
    # tts_uk/mykyta with rate=0.97 / length_scale=1.05 / volume=1.05 (no manual
    # knobs, meaning-first). Non-UK targets keep the legacy Edge default.
    _tgt = str(task_info.get("target_lang") or task_info.get("lang") or "").split("-")[0].lower()
    _uk_simple = _tgt == "uk"
    _max_atempo = float(HAPPY_PATH_MAX_ATEMPO_UK if _uk_simple else HAPPY_PATH_MAX_ATEMPO)
    _default_engine = "tts_uk" if _uk_simple else "edge-offline"
    policy = {
        "simple_pipeline": True,
        "simple_pipeline_steps": list(SIMPLE_PIPELINE_STEPS),
        "simple_disabled_modules": list(SIMPLE_DISABLED),
        # Timing / speech
        "min_atempo": float(HAPPY_PATH_MIN_ATEMPO),
        "max_atempo": _max_atempo,
        "max_atempo_hard": float(HAPPY_PATH_HARD_MAX_ATEMPO_UK if _uk_simple else HAPPY_PATH_MAX_ATEMPO),
        "no_speech_trim": bool(HAPPY_PATH_NO_SPEECH_TRIM),
        "text_fit_required": True,
        "post_tts_resegment_allowed": False,
        "blind_timing_align_allowed": False,
        # Like pyVideoTrans: finish with an MP4, don't stop at Studio.
        "simple_auto_mix": True,
        "tts_engine": str(task_info.get("tts_engine") or _default_engine).strip()
        or _default_engine,
        # Segmentation: Happy Path glue, never adaptive re-split.
        "segmentation_mode": "happy_path",
        # Stage 7: never fall into AI-Core streaming_text after MT.
        "tps_skip_orchestrator": True,
        "mt_path": "marian_batch",
        "simple_mt_locked": True,
        "translation_agent_path": False,
        "llm_adaptation_used": False,
        # Stage 8: fast STT defaults (small + beam1); no post-TTS re-STT.
        "simple_stt_locked": True,
        "voice_verification_asr_allowed": False,
        "post_tts_restt_allowed": False,
        "stt_engine": "faster-whisper",
        "stt_beam_size": 1,
        "stt_vad_filter": True,
        "stt_word_timestamps": False,
        # Stage 9: one Edge voice for the whole Simple clip (no Ostap/Polina flip).
        "simple_voice_locked": True,
        "voice_platform_multi_speaker_allowed": False,
    }
    if _uk_simple:
        # Stage 28 §F / Stage 29 §D — UK Simple defaults (only lang + volume
        # are user-facing). Segment targets ~4 / 7 / 12 s, aggressiveness medium.
        policy.setdefault("mykyta_rate", 0.97)
        policy.setdefault("mykyta_length_scale", 1.05)
        policy.setdefault("mykyta_volume", 1.05)
        policy.setdefault("mykyta_pitch", 0)
        policy.setdefault("segment_min_ms", 4000)
        policy.setdefault("segment_preferred_ms", 7000)
        policy.setdefault("segment_max_ms", 12000)
        policy.setdefault("segmentation_aggressiveness", 0.50)
        policy.setdefault("aggressiveness", "medium")
    task_info.update(policy)
    try:
        from engines.simple_stt_policy import apply_simple_stt_policy

        apply_simple_stt_policy(
            task_info, requested_model=str(task_info.get("model_size") or "") or None
        )
    except Exception as _stt_pol_exc:
        logger.debug("simple_stt policy stamp skipped: %s", _stt_pol_exc)
    logger.info(
        "simple_pipeline: policy ON mode=%s auto_mix=%s atempo=%.2f–%.2f stt=%s",
        task_info.get("user_mode"),
        task_info.get("simple_auto_mix"),
        float(task_info.get("min_atempo") or 0.95),
        float(task_info.get("max_atempo") or 1.08),
        task_info.get("stt_model") or task_info.get("model_size"),
    )
    return task_info


def is_simple_pipeline(task_info: dict[str, Any] | None = None) -> bool:
    info = task_info or {}
    if info.get("simple_pipeline"):
        return True
    try:
        from engines.happy_path import is_simple_mode, skip_advanced_text_shorteners

        return bool(is_simple_mode(info) or skip_advanced_text_shorteners(info))
    except Exception:
        return bool(info.get("happy_path"))


def should_auto_mix_mp4(task_info: dict[str, Any] | None = None) -> bool:
    """Simple path always muxes to MP4 (no Studio-only stop)."""
    info = task_info or {}
    if info.get("simple_auto_mix"):
        return True
    return is_simple_pipeline(info)


def run_simple_dub_pipeline(
    *,
    task_id: str,
    video_path: str,
    target_lang: str = "uk",
    voice: str | None = None,
    model_size: str = "tiny",
    source_lang: str | None = "en",
    ui_lang: str = "ru",
    mix_mode: str = "replace",
    mix_volumes: dict[str, Any] | None = None,
    keep_original_track: bool = False,
    dub_mode: str = "replace",
    mix_volume: float = 0.3,
    target_duration_ms: int | None = None,
    skip_translate: bool = False,
    skip_tts: bool = False,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    content_mode: str = "movie",
    dub_style: str = "modern",
) -> Any:
    """Explicit Simple entrypoint — policy first, then the shared pipeline body.

    Does not invent a second STT/MT/TTS stack; it locks Simple gates and
    reuses ``api.auto_dub_api._run_pipeline``.
    """
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

    with STATE_LOCK:
        task = AUTO_TASKS.get(str(task_id))
        if not task:
            raise RuntimeError(f"simple_pipeline: unknown task_id={task_id}")
        info = task.setdefault("info", {})
        info["user_mode"] = info.get("user_mode") or "basic"
        apply_simple_pipeline_policy(info, user_mode=str(info.get("user_mode") or "basic"))

    from api.auto_dub_api import _run_pipeline

    logger.info(
        "run_simple_dub_pipeline: task=%s steps=%s",
        task_id,
        ",".join(SIMPLE_PIPELINE_STEPS),
    )
    # Stage 28 §F — UK Simple defaults to tts_uk/mykyta (fallback resolves in
    # force_uk_tts_identity: Edge uk-UA-Ostap only if tts_uk missing).
    return _run_pipeline(
        task_id=task_id,
        video_path=video_path,
        target_lang=target_lang,
        voice=voice
        or (
            "mykyta"
            if str(target_lang or "").startswith("uk")
            else "en-US-GuyNeural"
        ),
        model_size=model_size,
        mix_mode=mix_mode,
        mix_volumes=mix_volumes or {},
        keep_original_track=keep_original_track,
        dub_mode=dub_mode,
        mix_volume=mix_volume,
        source_lang=source_lang,
        target_duration_ms=target_duration_ms,
        skip_translate=skip_translate,
        ui_lang=ui_lang,
        segmentation_mode="happy_path",
        ocr_enabled=False,
        dub_style=dub_style,
        skip_tts=skip_tts,
        tts_rate=tts_rate,
        tts_pitch=tts_pitch,
        content_mode=content_mode,
    )
