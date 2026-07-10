"""Mix Agent — stub wrapper around studio mix / dub engine."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.debug_helpers import finalize_agent_status
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

logger = logging.getLogger("tubedub.ai_core.mix_agent")


class MixAgent:
    """Wrap run_studio_mix_internal via state hook."""

    VERSION = "1.0"

    def __init__(
        self,
        output_dir: Path | None = None,
        *,
        mix_hook: Callable[..., Any] | None = None,
    ):
        self.output_dir = output_dir
        self._mix_hook = mix_hook

    def run(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> AgentExecutionResult:
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        hook: Callable[..., Any] | None = self._mix_hook or state.get("mix_hook")
        updated: dict[str, Any] = {"mix_agent_path": True}

        if hook is None:
            if not (IS_DEBUG_LEARNING_MODE() or state.get("auto_mix")):
                warnings.append("mix_hook_missing")
                elapsed = round((time.perf_counter() - t0) * 1000, 1)
                return AgentExecutionResult(
                    status="warning",
                    updated_state=updated,
                    metrics={},
                    warnings=warnings,
                    errors=[],
                    execution_time_ms=elapsed,
                    decision_log=["mix_skipped"],
                )
            try:
                from api.studio_api import run_studio_mix_internal

                ok, out_path, mix_errs = run_studio_mix_internal(task_id, force=True)
                updated["mix_output"] = out_path
                updated["mix_ok"] = ok
                if mix_errs:
                    warnings.extend(mix_errs)
                status = finalize_agent_status("success" if ok else "warning")
            except Exception as exc:
                logger.warning("[MixAgent] internal mix failed: %s", exc)
                warnings.append(str(exc))
                status = finalize_agent_status("warning")
        else:
            try:
                out = hook(task_id, manifest, state)
                if isinstance(out, dict):
                    updated.update(out)
                status = finalize_agent_status("success")
            except Exception as exc:
                logger.warning("[MixAgent] mix hook failed: %s", exc)
                status = finalize_agent_status("warning" if IS_DEBUG_LEARNING_MODE() else "error")
                if IS_DEBUG_LEARNING_MODE():
                    warnings.append(str(exc))
                else:
                    errors.append(str(exc))

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        music_preserved = False
        mix_quality = 0.0
        try:
            from engines.dub_quality_stabilization import compute_mix_quality_heuristic

            task_info = state.get("task_info") or {}
            sep = task_info.get("source_separation") or {}
            final_mix = task_info.get("final_mix") or updated.get("final_mix") or {}
            music_preserved = bool(
                sep.get("success") and (final_mix.get("used_stem_mix") or updated.get("mix_used_stem"))
            )
            mix_quality = compute_mix_quality_heuristic(
                separation_success=bool(sep.get("success")),
                used_stem_mix=bool(final_mix.get("used_stem_mix") or updated.get("mix_used_stem")),
                music_detected=bool(final_mix.get("music_detected_in_final")),
                fallback_used=bool(final_mix.get("fallback_used") or sep.get("fallback_used")),
            )
            updated["music_preserved"] = music_preserved
            updated["mix_quality"] = mix_quality
        except Exception:
            pass

        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "Mix/v1",
                called=True,
                success=status != "error",
                error="; ".join(errors) if errors else None,
                decision="mix_complete" if status == "success" else "mix_fallback",
                fallback_used=bool(warnings),
                execution_time_ms=elapsed,
                output_metrics={
                    "mix_output": updated.get("mix_output"),
                    "music_preserved": music_preserved,
                    "mix_quality": mix_quality,
                },
            )
        except Exception:
            pass

        return AgentExecutionResult(
            status=status,
            updated_state=updated,
            metrics={
                "mix_output": updated.get("mix_output"),
                "music_preserved": music_preserved,
                "mix_quality": mix_quality,
            },
            warnings=warnings,
            errors=errors,
            execution_time_ms=elapsed,
            decision_log=["studio_mix"],
        )
