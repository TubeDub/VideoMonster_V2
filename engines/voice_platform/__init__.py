"""TTS Platform • Voice Intelligence • Lip Sync 2.0 — Master Spec Part 7."""

from __future__ import annotations

from engines.voice_platform.engine import (
    plan_project_voices,
    run_voice_platform_for_meta,
    synthesize,
)
from engines.voice_platform.planner import VoiceMemory, plan_multi_speaker, plan_voice_for_unit
from engines.voice_platform.provider import VoiceProvider
from engines.voice_platform.tts_registry import get_provider, list_providers, register_provider
from engines.voice_platform.types import (
    EMOTIONS,
    LipSyncData,
    SynthesisRequest,
    SynthesisResult,
    VoicePlan,
)
from engines.voice_platform.voice_registry import load_voice_registry, list_style_profiles

__all__ = [
    "EMOTIONS",
    "LipSyncData",
    "SynthesisRequest",
    "SynthesisResult",
    "VoiceMemory",
    "VoicePlan",
    "VoiceProvider",
    "get_provider",
    "list_providers",
    "list_style_profiles",
    "load_voice_registry",
    "plan_multi_speaker",
    "plan_project_voices",
    "plan_voice_for_unit",
    "register_provider",
    "run_voice_platform_for_meta",
    "synthesize",
]
