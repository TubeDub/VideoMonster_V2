"""Voice Verification Agent — post-TTS ASR quality gate before Mix."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.debug_helpers import finalize_agent_status
from engines.ai_core.voice_verification_agent.verification_loop import (
    run_voice_verification_loop,
)
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.voice_verification_agent")

_APP_DIR = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _APP_DIR / "output"


def write_voice_verification_report(
    task_id: str,
    loop_log: list[dict[str, Any]],
    *,
    summary: dict[str, Any],
    project_uuid: str = "",
    app_dir: Path | None = None,
) -> Path:
    base = app_dir or _APP_DIR
    payload = {
        "task_id": task_id,
        "project_uuid": project_uuid,
        "agent": "VoiceVerification/v1",
        "summary": summary,
        "segment_count": len(loop_log),
        "passed_count": sum(1 for r in loop_log if r.get("pass")),
        "failed_count": sum(1 for r in loop_log if not r.get("pass")),
        "segments": loop_log,
    }
    diag_dir = base / "output" / "diagnostics" / task_id
    diag_dir.mkdir(parents=True, exist_ok=True)
    path = diag_dir / "voice_verification_report.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    if project_uuid:
        man_dir = base / "output" / "manifests" / project_uuid
        man_dir.mkdir(parents=True, exist_ok=True)
        with open(man_dir / "voice_verification_report.json", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


class VoiceVerificationAgent:
    """Verify synthesized speech via ASR; route failures to responsible agents."""

    VERSION = "1.0"

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or _OUTPUT_DIR

    def run(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> AgentExecutionResult:
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []

        segments_in = list(state.get("segments") or state.get("segments_data") or [])
        if not segments_in:
            return AgentExecutionResult(
                status="warning",
                updated_state={"voice_verification_done": True},
                metrics={},
                warnings=["no_segments"],
                errors=[],
                execution_time_ms=0.0,
                decision_log=["no_segments"],
            )

        target_lang = normalize_lang(
            manifest.get("target_lang") or state.get("target_lang") or "uk"
        )
        resolve_audio: Callable | None = state.get("voice_verification_resolve_audio")
        regen_voice: Callable | None = state.get("voice_verification_regen")

        working_state = dict(state)
        working_state["segments"] = list(segments_in)

        segments, loop_log = run_voice_verification_loop(
            working_state["segments"],
            manifest=manifest,
            state=working_state,
            task_id=task_id,
            target_lang=target_lang,
            resolve_audio=resolve_audio,
            regen_voice=regen_voice,
            on_progress=state.get("voice_verification_progress"),
        )

        passed = sum(1 for r in loop_log if r.get("pass"))
        retried = sum(int(r.get("retry_count") or 0) for r in loop_log)
        checked = len(loop_log)

        for entry in loop_log:
            idx = entry.get("index")
            for ev in entry.get("events") or []:
                decision_log.append(
                    f"segment_{idx}:vv_retry={ev.get('attempt')} "
                    f"route={ev.get('route_to')} issues={ev.get('issues')}"
                )
            if not entry.get("pass"):
                warnings.append(
                    f"segment_{idx}:voice_verification_failed "
                    f"similarity={(entry.get('final_metrics') or {}).get('similarity')}"
                )

        all_pass = passed == checked and checked > 0
        status = "success" if all_pass else "warning"
        if checked == 0 and IS_DEBUG_LEARNING_MODE():
            status = "success"
        elif checked > 0 and passed == 0 and not IS_DEBUG_LEARNING_MODE():
            status = "error"
            errors.append("all_segments_failed_voice_verification")
        status = finalize_agent_status(status)

        summary = {
            "segment_count": checked,
            "passed_count": passed,
            "failed_count": checked - passed,
            "retries": retried,
        }

        report_path = write_voice_verification_report(
            task_id,
            loop_log,
            summary=summary,
            project_uuid=str(manifest.get("project_uuid") or ""),
            app_dir=self.output_dir.parent if self.output_dir.name == "output" else _APP_DIR,
        )

        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "VoiceVerification/v1",
                called=True,
                success=status != "error",
                decision=f"passed={passed}/{checked}",
                retry_count=retried,
                output_metrics=summary,
            )
            open_ddf.save(task_id)
        except Exception:
            pass

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "segments_data": segments,
                "voice_verification_done": True,
                "voice_verification_passed": all_pass,
                "voice_verification_agent_path": True,
                "voice_verification_report_path": str(report_path),
                "voice_verification_report": summary,
                "voice_verification_loop_log": loop_log,
                "studio_ready": all_pass or IS_DEBUG_LEARNING_MODE(),
            },
            metrics={"summary": summary, "per_segment": loop_log},
            warnings=warnings,
            errors=errors,
            execution_time_ms=elapsed,
            decision_log=decision_log,
        )
