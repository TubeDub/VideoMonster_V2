"""Voice Preparation Agent — READ ONLY validation before TTS."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.debug_helpers import finalize_agent_status
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

logger = logging.getLogger("tubedub.ai_core.voice_preparation_agent")

_EMOTION_TAG_RE = re.compile(r"\[(?:emotion|emo|mood):[^\]]+\]", re.I)


class VoicePreparationAgent:
    """Validate grammar_text and emotion tags are ready for TTS."""

    VERSION = "1.0"

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir

    def run(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> AgentExecutionResult:
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        segments = list(state.get("segments") or state.get("segments_data") or [])
        ready = 0
        from engines.translation_validation import resolve_post_quality_text

        for seg in segments:
            text = resolve_post_quality_text(seg).strip()
            if not text:
                warnings.append(f"segment_{seg.get('index', '?')}:empty_voice_input")
                continue

            try:
                from engines.ai_core.platform.feature_registry import is_platform_feature_enabled
                from engines.ai_core.quality_gate import get_quality_gate

                if is_platform_feature_enabled("quality_gate"):
                    gate = get_quality_gate()
                    pre = gate.run_pre_tts(
                        text,
                        tgt_lang=str(state.get("target_lang") or manifest.get("target_lang") or "ru"),
                        segment=seg,
                    )
                    if not pre.passed:
                        warnings.append(
                            f"segment_{seg.get('index', '?')}:pre_tts_gate_failed"
                        )
                        if not IS_DEBUG_LEARNING_MODE():
                            continue
                    text = pre.text or text
            except Exception:
                pass

            ready += 1
            seg["voice_input"] = text
            seg["final_text"] = text
            brief = seg.get("creative_brief") or {}
            if brief:
                seg["voice_prep_emotion"] = brief.get("emotion")
                seg["voice_prep_speed"] = brief.get("speaking_speed")
                seg["voice_prep_intensity"] = brief.get("emotional_intensity")
            if _EMOTION_TAG_RE.search(text):
                seg["emotion_tags_present"] = True

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        status = "success" if ready > 0 or not segments else "warning"
        if not ready and segments and not IS_DEBUG_LEARNING_MODE():
            errors.append("no_segments_ready_for_tts")
            status = "error"
        status = finalize_agent_status(status)

        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "VoicePrep/v1",
                called=True,
                success=status != "error",
                decision="voice_prep_validated",
                execution_time_ms=elapsed,
                input_metrics={"segment_count": len(segments)},
                output_metrics={"ready_count": ready},
            )
        except Exception:
            pass

        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "voice_preparation_done": True,
                "voice_prep_ready_count": ready,
            },
            metrics={
                "ready_count": ready,
                "segment_count": len(segments),
                "input_summary": {"segment_count": len(segments)},
                "output_summary": {"ready_count": ready},
            },
            warnings=warnings,
            errors=errors,
            execution_time_ms=elapsed,
            decision_log=["validate_grammar_text_for_tts"],
        )
