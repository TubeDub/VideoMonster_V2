"""Voice verification loop — route failures back to responsible agents."""

from __future__ import annotations

import logging
from typing import Any, Callable

from engines.ai_core.voice_verification_agent.asr_compare import (
    MAX_VERIFICATION_CYCLES,
    verify_segment_audio,
)
from engines.mt.lang_codes import normalize_lang
from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts
from engines.translation_validation import resolve_post_quality_text

logger = logging.getLogger("tubedub.ai_core.voice_verification.loop")

RegenVoiceCallback = Callable[[int, dict[str, Any], str], dict[str, Any] | None]
ResolveAudioCallback = Callable[[dict[str, Any]], Any]
ProgressCallback = Callable[..., None]


def _slot_ms(seg: dict[str, Any]) -> int | None:
    if seg.get("timing_slot_ms") is not None:
        try:
            return int(seg["timing_slot_ms"])
        except (TypeError, ValueError):
            pass
    start = seg.get("start_ms") or seg.get("start")
    end = seg.get("end_ms") or seg.get("end")
    if start is not None and end is not None:
        try:
            if float(end) < 1000:
                return int((float(end) - float(start)) * 1000)
            return int(float(end) - float(start))
        except (TypeError, ValueError):
            pass
    playback = seg.get("playback_duration") or seg.get("tts_ms")
    if playback is not None:
        try:
            return int(playback)
        except (TypeError, ValueError):
            pass
    return None


def _expected_text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("tts_text")
        or resolve_post_quality_text(seg)
        or resolve_segment_text_for_tts(seg)
        or ""
    ).strip()


def _resolve_wav(seg: dict[str, Any], resolve_audio: ResolveAudioCallback | None) -> Any:
    if resolve_audio:
        return resolve_audio(seg)
    from pathlib import Path

    for key in ("tts_file_path", "file", "fitted_file"):
        val = seg.get(key)
        if val:
            p = Path(str(val))
            if p.is_file():
                return p
            candidate = Path("output") / str(val)
            if candidate.is_file():
                return candidate
    return None


def route_and_fix_segment(
    seg: dict[str, Any],
    metrics,
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    segment_index: int,
    regen_voice: RegenVoiceCallback | None,
) -> dict[str, Any]:
    route = str(metrics.route_to or "voice")
    agent_map = {
        "semantic": "SemanticAgent",
        "grammar": "GrammarAgent",
        "timing": "TimingAgent",
        "voice": "VoiceAgent",
    }
    agent_name = agent_map.get(route)

    if agent_name and agent_name != "VoiceAgent":
        from engines.ai_core.quality_agent.retry_orchestrator import rerun_agent_for_segment

        llm_routes = {"SemanticAgent", "GrammarAgent"}
        if agent_name in llm_routes:
            try:
                from engines.ai_core.quality_agent.retry_orchestrator import _llm_available

                if not _llm_available():
                    logger.info(
                        "Voice verify seg=%s: skip %s — LLM unavailable",
                        segment_index,
                        agent_name,
                    )
                    agent_name = None
            except Exception:
                pass

        if agent_name:
            updated = rerun_agent_for_segment(
                agent_name,
                segment_index,
                manifest,
                state,
                task_id,
            )
            if updated:
                seg = updated
                state.setdefault("segments", [])
                if segment_index < len(state["segments"]):
                    state["segments"][segment_index] = seg

        if route == "timing":
            from engines.ai_core.quality_agent.retry_orchestrator import rerun_agent_for_segment

            refreshed = rerun_agent_for_segment(
                "GrammarAgent",
                segment_index,
                manifest,
                state,
                task_id,
            )
            if refreshed:
                seg = refreshed
                if segment_index < len(state["segments"]):
                    state["segments"][segment_index] = seg

    if regen_voice:
        regen_reason = route if route != "voice" else "voice_resynth"
        regen_result = regen_voice(segment_index, seg, regen_reason)
        if regen_result:
            seg = regen_result

    return seg


def run_voice_verification_loop(
    segments: list[dict[str, Any]],
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    target_lang: str,
    resolve_audio: ResolveAudioCallback | None = None,
    regen_voice: RegenVoiceCallback | None = None,
    max_cycles: int = MAX_VERIFICATION_CYCLES,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify each segment WAV; retry up to max_cycles with agent routing."""
    tgt = normalize_lang(target_lang)
    loop_log: list[dict[str, Any]] = []

    active_indices = [
        i for i, seg in enumerate(segments) if seg.get("merged_into") is None
    ]
    total_active = len(active_indices)

    for pos, i in enumerate(active_indices):
        seg = segments[i]
        if on_progress:
            on_progress(
                segment_index=i,
                position=pos + 1,
                total=total_active,
                attempt=0,
                route="",
            )

        slot_ms = _slot_ms(seg)
        expected = _expected_text(seg)
        source = str(seg.get("text") or seg.get("original_text") or "").strip()
        attempts = int(seg.get("voice_verification_retry_count") or 0)
        events: list[dict[str, Any]] = []

        wav_path = _resolve_wav(seg, resolve_audio)
        metrics = verify_segment_audio(
            expected_text=expected,
            wav_path=wav_path,
            target_lang=tgt,
            slot_ms=slot_ms,
            source_text=source,
        )
        seg["voice_verification_asr_text"] = metrics.recognized_text

        while not metrics.passed and attempts < max_cycles:
            if on_progress:
                on_progress(
                    segment_index=i,
                    position=pos + 1,
                    total=total_active,
                    attempt=attempts + 1,
                    route=str(metrics.route_to or ""),
                )
            events.append(
                {
                    "attempt": attempts + 1,
                    "route_to": metrics.route_to,
                    "issues": list(metrics.issues),
                    "expected_preview": expected[:120],
                    "recognized_preview": "",
                    "metrics": metrics.to_dict(),
                }
            )
            seg = route_and_fix_segment(
                seg,
                metrics,
                manifest=manifest,
                state=state,
                task_id=task_id,
                segment_index=i,
                regen_voice=regen_voice,
            )
            segments[i] = seg
            attempts += 1
            seg["voice_verification_retry_count"] = attempts

            expected = _expected_text(seg)
            wav_path = _resolve_wav(seg, resolve_audio)
            metrics = verify_segment_audio(
                expected_text=expected,
                wav_path=wav_path,
                target_lang=tgt,
                slot_ms=slot_ms,
                source_text=source,
            )
            seg["voice_verification_asr_text"] = metrics.recognized_text
            if events:
                events[-1]["recognized_preview"] = metrics.recognized_text[:120]

        seg["voice_verification_passed"] = metrics.passed
        seg["voice_verification_metrics"] = metrics.to_dict()
        if not metrics.passed:
            seg["voice_verification_issues"] = metrics.issues
            seg["voice_verification_route_to"] = metrics.route_to

        loop_log.append(
            {
                "index": i,
                "segment_id": seg.get("segment_id"),
                "pass": metrics.passed,
                "retry_count": attempts,
                "expected_text": expected,
                "recognized_text": seg.get("voice_verification_asr_text", ""),
                "final_metrics": metrics.to_dict(),
                "events": events,
            }
        )

    return segments, loop_log
