"""Voice Platform orchestrator — Master Spec Part 7."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from engines.voice_platform.cache import VoiceCache, cache_key
from engines.voice_platform.failover import synthesize_with_failover
from engines.voice_platform.lipsync import build_lipsync_data, lipsync_from_speech_units
from engines.voice_platform.metrics import check_performance_budget, get_metrics, record_synthesis
from engines.voice_platform.planner import (
    VoiceMemory,
    assert_voice_consistency,
    plan_multi_speaker,
    plan_voice_for_unit,
)
from engines.voice_platform.quality import validate_synthesis_audio
from engines.voice_platform.tts_registry import get_provider, list_providers
from engines.voice_platform.types import SynthesisRequest, SynthesisResult, VoicePlan
from engines.voice_platform.voice_registry import load_voice_registry, resolve_voice

logger = logging.getLogger("tubedub.voice_platform")


def _raw_synthesize(request: SynthesisRequest) -> SynthesisResult:
    voice = resolve_voice(voice_uuid=request.voice_uuid)
    provider = get_provider(request.provider or voice.provider)
    external = voice.external_id or voice.voice_uuid
    out = request.output_path
    if not out:
        out = str(
            Path("output")
            / "voice_platform"
            / f"{request.speech_uuid or uuid.uuid4().hex[:8]}.wav"
        )
    result = provider.synthesize(
        request.text,
        external,
        out,
        rate=request.rate,
        pitch=request.pitch,
        emotion=request.emotion,
        language=request.language or voice.language,
    )
    result.voice_uuid = voice.voice_uuid
    result.speech_uuid = request.speech_uuid
    return result


def synthesize(request: SynthesisRequest, *, cache: VoiceCache | None = None) -> SynthesisResult:
    """
    Full path: cache → provider (with failover) → quality → lipsync data.
    Does not mutate request.text (P623).
    """
    text_before = request.text
    cache = cache or VoiceCache()
    voice = resolve_voice(voice_uuid=request.voice_uuid)
    provider_id = request.provider or voice.provider

    key = cache_key(
        text=request.text,
        voice_uuid=voice.voice_uuid,
        provider=provider_id,
        rate=request.rate,
        pitch=request.pitch,
        emotion=request.emotion,
        contract_version=request.contract_version,
    )

    if request.allow_cache:
        hit = cache.lookup(key)
        if hit is not None:
            dest = request.output_path or str(hit)
            if Path(dest).resolve() != hit.resolve():
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(hit, dest)
            else:
                dest = str(hit)
            lipsync = build_lipsync_data(
                request.speech_uuid or "",
                request.text,
            ).to_dict()
            qa = validate_synthesis_audio(dest)
            record_synthesis(
                provider=provider_id,
                elapsed_ms=0.0,
                ok=True,
                cached=True,
                quality=100.0 if qa.get("ok") else 50.0,
            )
            assert text_before == request.text
            return SynthesisResult(
                ok=True,
                output_path=dest,
                provider=provider_id,
                voice_uuid=voice.voice_uuid,
                speech_uuid=request.speech_uuid,
                cached=True,
                quality=qa,
                lipsync=lipsync,
                meta={"cache_key": key},
            )

    req = SynthesisRequest(
        **{
            **request.to_dict(),
            "voice_uuid": voice.voice_uuid,
            "provider": provider_id,
        }
    )
    t0 = time.perf_counter()
    result = synthesize_with_failover(req, synthesize_fn=_raw_synthesize)
    elapsed = (time.perf_counter() - t0) * 1000
    result.elapsed_ms = result.elapsed_ms or elapsed

    if result.ok and result.output_path:
        qa = validate_synthesis_audio(result.output_path)
        result.quality = qa
        if not qa.get("ok"):
            # Soft: mark issues but keep file for Decision Layer
            result.meta = dict(result.meta or {})
            result.meta["quality_issues"] = qa.get("issues")
        if request.allow_cache:
            try:
                cache.store(key, result.output_path, meta={"voice_uuid": voice.voice_uuid})
            except Exception as exc:
                logger.debug("cache store failed: %s", exc)
        result.lipsync = build_lipsync_data(
            request.speech_uuid or "",
            request.text,
            duration_ms=float((qa.get("metrics") or {}).get("duration_ms") or 1000),
        ).to_dict()

    record_synthesis(
        provider=result.provider or provider_id,
        elapsed_ms=result.elapsed_ms,
        ok=result.ok,
        cached=False,
        retried=bool((result.meta or {}).get("failover_attempt", 1) > 1),
        quality=100.0 if (result.quality or {}).get("ok") else 0.0,
    )
    check_performance_budget(last_synth_ms=result.elapsed_ms, cache_bytes=cache.stats().get("bytes"))
    assert text_before == request.text
    return result


def plan_project_voices(
    speech_units: list[Any],
    *,
    project_id: str = "",
    style: str = "Movie",
    language: str = "ru",
    preferred_voice: str | None = None,
    preferred_voices: dict[str, str] | None = None,
    memory: VoiceMemory | None = None,
) -> dict[str, Any]:
    """Build VoicePlans + Lip Sync 2.0 for a set of speech units (no synthesis)."""
    load_voice_registry()
    units: list[dict[str, Any]] = []
    for u in speech_units:
        if isinstance(u, dict):
            units.append(u)
        else:
            units.append(
                {
                    "speech_uuid": getattr(u, "speech_uuid", ""),
                    "speaker_uuid": getattr(u, "speaker_uuid", None) or getattr(u, "speaker", ""),
                    "text": getattr(u, "text", ""),
                    "emotion": getattr(u, "emotion", "calm"),
                    "style": getattr(u, "style", style) or style,
                    "language": language,
                }
            )
    prefs = dict(preferred_voices or {})
    if preferred_voice and units:
        # Apply default preferred voice to first speaker only if not set
        first_speaker = str(units[0].get("speaker_uuid") or units[0].get("speech_uuid"))
        prefs.setdefault(first_speaker, preferred_voice)

    plans, mem = plan_multi_speaker(
        units,
        project_id=project_id,
        default_style=style,
        default_language=language,
        preferred_voices=prefs,
        memory=memory,
    )
    lipsync = lipsync_from_speech_units(units)
    consistency = assert_voice_consistency(mem)
    return {
        "version": "7.0",
        "plans": [p.to_dict() for p in plans],
        "memory": mem.to_dict(),
        "lipsync": lipsync,
        "consistency_issues": consistency,
        "providers": list_providers(),
        "metrics": get_metrics(),
    }


def run_voice_platform_for_meta(
    meta: dict[str, Any],
    *,
    voice: str | None = None,
    language: str = "ru",
    style: str = "Movie",
    project_id: str = "",
) -> dict[str, Any]:
    """Attach Voice Platform plan to Semantic/Dub meta (planning only)."""
    speech = meta.get("speech_units") or (meta.get("dub") or {}).get("speech_units") or []
    payload = plan_project_voices(
        speech,
        project_id=project_id,
        style=style,
        language=language,
        preferred_voice=voice,
    )
    return payload
