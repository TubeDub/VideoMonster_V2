"""
Heavy LLM kill-switch (TubeDub engine-first mode).

User policy: the dubbing product must run on its OWN engine
(MT + rules + Whisper STT + Demucs + TTS + FFmpeg). Heavy generative LLM
models (Qwen / DeepSeek / Ollama / cloud chat) are OFF by default — they
cause multi-minute stalls on CPU and are not required for a correct dub.

What stays ON (engine, not generative LLM):
  * Whisper / Faster-Whisper  — speech-to-text
  * Demucs / ffmpeg stems     — voice vs music+SFX separation
  * Marian / Argos / deep-translator — deterministic machine translation
  * Rule-based naturalizer    — calques, word order, entities
  * Edge-TTS / offline TTS    — speech synthesis
  * FFmpeg / DubEngine        — mix & mux

What this switch turns OFF:
  * Ollama / LM Studio / OpenAI-compatible chat calls
  * Qwen / DeepSeek / Llama chat adaptation
  * AI Director LLM creative briefs
  * CATP / LLM naturalizer polish
  * AI Assistant cloud chat

Override (developer only): set ``VM_ENABLE_HEAVY_LLM=1`` or
``FEATURE_HEAVY_LLM=1`` to re-enable.
"""

from __future__ import annotations

import os

ENV_ENABLE = "VM_ENABLE_HEAVY_LLM"
ENV_DISABLE = "VM_DISABLE_HEAVY_LLM"
FEATURE_ID = "heavy_llm"


def _truthy(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _falsy(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in ("0", "false", "no", "off")


def is_heavy_llm_disabled() -> bool:
    """True when heavy LLM models must not be used (DEFAULT).

    Precedence:
      1. ``VM_DISABLE_HEAVY_LLM`` / ``VM_ENABLE_HEAVY_LLM`` env (explicit)
      2. feature flag ``heavy_llm`` (enabled ⇒ LLM allowed)
      3. default: DISABLED (engine-first)
    """
    if _truthy(os.getenv(ENV_DISABLE)):
        return True
    if _truthy(os.getenv(ENV_ENABLE)):
        return False
    if _falsy(os.getenv(ENV_ENABLE)) and os.getenv(ENV_ENABLE) is not None:
        return True
    try:
        from engines.core.feature_flags import is_enabled

        # Flag ON means heavy LLM is allowed; OFF / missing → disabled.
        if is_enabled(FEATURE_ID, developer_session=True):
            return False
        return True
    except Exception:
        return True


def heavy_llm_disabled_reason() -> str:
    return (
        "Тяжёлые LLM (Qwen/DeepSeek/Ollama) отключены. "
        "Дубляж идёт на собственном движке TubeDub "
        "(MT + правила + Whisper + Demucs + TTS). "
        "Для включения: VM_ENABLE_HEAVY_LLM=1"
    )
