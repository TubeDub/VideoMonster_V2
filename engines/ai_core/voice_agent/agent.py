"""Voice Agent — stub wrapper around the existing TTS loop."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.debug_helpers import finalize_agent_status
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

logger = logging.getLogger("tubedub.ai_core.voice_agent")


class VoiceAgent:
    """Invoke TTS via state hook; record OpenDDF either way."""

    VERSION = "1.0"

    def __init__(
        self,
        output_dir: Path | None = None,
        *,
        tts_hook: Callable[..., Any] | None = None,
    ):
        self.output_dir = output_dir
        self._tts_hook = tts_hook

    def run(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> AgentExecutionResult:
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        hook: Callable[..., Any] | None = self._tts_hook or state.get("tts_hook")
        updated: dict[str, Any] = {"voice_agent_path": True}

        if hook is None:
            warnings.append("tts_hook_missing")
            status = finalize_agent_status("warning")
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            self._ddf(task_id, status, elapsed, warnings, errors)
            return AgentExecutionResult(
                status=status,
                updated_state=updated,
                metrics={"tts_segments": 0},
                warnings=warnings,
                errors=errors,
                execution_time_ms=elapsed,
                decision_log=["tts_hook_missing"],
            )

        try:
            out = hook(task_id, manifest, state)
            if isinstance(out, dict):
                updated.update(out)
            segments_done = int(updated.get("tts_segments_done") or 0)
            status = finalize_agent_status("success")
        except Exception as exc:
            logger.warning("[VoiceAgent] TTS hook failed: %s", exc)
            errors.append(str(exc))
            status = finalize_agent_status("warning" if IS_DEBUG_LEARNING_MODE() else "error")
            segments_done = 0
            if IS_DEBUG_LEARNING_MODE():
                errors = []
                warnings.append(str(exc))

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        self._ddf(task_id, status, elapsed, warnings, errors, segments_done)
        return AgentExecutionResult(
            status=status,
            updated_state=updated,
            metrics={
                "tts_segments_done": segments_done,
                "output_summary": {"tts_segments_done": segments_done},
            },
            warnings=warnings,
            errors=errors,
            execution_time_ms=elapsed,
            decision_log=["tts_hook"],
        )

    def _ddf(
        self,
        task_id: str,
        status: str,
        elapsed: float,
        warnings: list[str],
        errors: list[str],
        segments_done: int = 0,
    ) -> None:
        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "Voice/v1",
                called=True,
                success=status != "error",
                error="; ".join(errors) if errors else None,
                decision="tts_complete" if status == "success" else "tts_fallback",
                fallback_used=bool(warnings),
                execution_time_ms=elapsed,
                output_metrics={"tts_segments_done": segments_done},
            )
        except Exception:
            pass
