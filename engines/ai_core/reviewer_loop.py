"""Reviewer-controlled text pipeline — audit, route, retry loop."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engines.dub_quality_stabilization import (
    MAX_REVIEWER_RETRIES,
    audit_segment_for_reviewer,
)
from engines.reviewer_scores import enrich_segment_scores
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.reviewer_loop")

SLOT_FIT_PASS = 0.85
GRAMMAR_SCORE_PASS = 0.85

TEXT_AGENT_CHAIN = ("semantic", "timing", "grammar", "quality", "reviewer")

_APP_DIR = Path(__file__).resolve().parents[2]


def route_failed_segment(
    seg: dict[str, Any],
    audit: dict[str, Any],
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    segment_index: int,
) -> dict[str, Any]:
    """Send one segment back to the agent that failed the review."""
    route = str(audit.get("route_to") or "").strip()
    if not route:
        return seg

    agent_map = {
        "translation": "TranslationAgent",
        "semantic": "SemanticAgent",
        "timing": "TimingAgent",
        "grammar": "GrammarAgent",
    }
    agent_name = agent_map.get(route)
    if not agent_name:
        return seg

    from engines.ai_core.ai_network.bridge import emit_recovery_action

    emit_recovery_action(
        task_id,
        from_agent="reviewer",
        to_agent=route,
        segment_index=int(seg.get("index", segment_index)),
        reason=str(audit.get("reason") or "reviewer_fail"),
    )

    from engines.ai_core.quality_agent.retry_orchestrator import rerun_agent_for_segment

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

    # Downstream refresh: timing fix may invalidate grammar
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

    return seg


def run_reviewer_loop_for_segments(
    segments: list[dict[str, Any]],
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    source_lang: str,
    target_lang: str,
    registry=None,
    max_retries: int = MAX_REVIEWER_RETRIES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-segment reviewer loop with routing to responsible agents."""
    from engines.dub_quality_stabilization import apply_reviewer_repairs

    tgt = normalize_lang(target_lang)
    src = normalize_lang(source_lang)
    loop_log: list[dict[str, Any]] = []

    for i, seg in enumerate(segments):
        enrich_segment_scores(seg, tgt_lang=tgt)

        slot_ms = seg.get("timing_slot_ms")
        start = seg.get("start")
        end = seg.get("end")
        if slot_ms is None and start is not None and end is not None:
            try:
                slot_ms = int((float(end) - float(start)) * 1000)
            except (TypeError, ValueError):
                slot_ms = None

        audit = audit_segment_for_reviewer(
            seg,
            source_lang=src,
            target_lang=tgt,
            slot_ms=slot_ms,
            slot_fit_threshold=SLOT_FIT_PASS,
            grammar_threshold=GRAMMAR_SCORE_PASS,
        )
        attempts = int(seg.get("reviewer_retry_count") or 0)
        seg_events: list[dict[str, Any]] = []

        while not audit.get("pass") and attempts < max_retries:
            route = str(audit.get("route_to") or "")
            issues = list(audit.get("issues") or [])
            seg_events.append(
                {
                    "attempt": attempts + 1,
                    "route_to": route,
                    "issues": issues,
                    "slot_fit": audit.get("slot_fit_score"),
                    "grammar_score": audit.get("grammar_score"),
                }
            )

            if route == "translation" or "empty_text" in issues or "missing_translation" in issues:
                repaired, actions = apply_reviewer_repairs(
                    seg,
                    audit,
                    source_lang=src,
                    target_lang=tgt,
                    registry=registry,
                )
                seg_events[-1]["inline_actions"] = actions
                if not repaired and route:
                    seg = route_failed_segment(
                        seg,
                        audit,
                        manifest=manifest,
                        state=state,
                        task_id=task_id,
                        segment_index=i,
                    )
                    segments[i] = seg
                    seg_events[-1]["routed_agent"] = route
            else:
                seg = route_failed_segment(
                    seg,
                    audit,
                    manifest=manifest,
                    state=state,
                    task_id=task_id,
                    segment_index=i,
                )
                segments[i] = seg
                seg_events[-1]["routed_agent"] = route

            attempts += 1
            seg["reviewer_retry_count"] = attempts
            enrich_segment_scores(seg, tgt_lang=tgt)
            audit = audit_segment_for_reviewer(
                seg,
                source_lang=src,
                target_lang=tgt,
                slot_ms=slot_ms,
                slot_fit_threshold=SLOT_FIT_PASS,
                grammar_threshold=GRAMMAR_SCORE_PASS,
            )

        seg["reviewer_approved"] = bool(audit.get("pass"))
        if audit.get("pass"):
            from engines.translation_validation import resolve_post_quality_text

            final = resolve_post_quality_text(seg)
            if final:
                seg["final_text"] = final
                seg["voice_input"] = final
        else:
            seg["reviewer_issues"] = audit.get("issues")
            seg["reviewer_route_to"] = audit.get("route_to")

        loop_log.append(
            {
                "index": i,
                "segment_id": seg.get("segment_id"),
                "pass": audit.get("pass"),
                "retry_count": attempts,
                "final_audit": audit,
                "events": seg_events,
            }
        )

    return segments, loop_log


def write_reviewer_loop_json(
    task_id: str,
    loop_log: list[dict[str, Any]],
    *,
    project_uuid: str = "",
    app_dir: Path | None = None,
) -> Path:
    base = app_dir or _APP_DIR
    payload = {
        "task_id": task_id,
        "project_uuid": project_uuid,
        "segment_count": len(loop_log),
        "passed_count": sum(1 for r in loop_log if r.get("pass")),
        "failed_count": sum(1 for r in loop_log if not r.get("pass")),
        "segments": loop_log,
    }
    diag_dir = base / "output" / "diagnostics" / task_id
    diag_dir.mkdir(parents=True, exist_ok=True)
    path = diag_dir / "reviewer_loop.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    if project_uuid:
        man_dir = base / "output" / "manifests" / project_uuid
        man_dir.mkdir(parents=True, exist_ok=True)
        with open(man_dir / "reviewer_loop.json", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def run_controlled_text_pipeline(
    orchestrator: Any,
    task_id: str,
    video_path: str,
    manifest_path: str,
    state: dict[str, Any],
    *,
    max_reviewer_rounds: int = 2,
) -> Any:
    """Run text agents then reviewer loop; optional outer round if failures remain."""
    from engines.ai_core.orchestrator import PipelineResult

    working = dict(state)
    last_result: PipelineResult | None = None

    for round_num in range(1, max(1, max_reviewer_rounds) + 1):
        last_result = orchestrator.run_pipeline(
            task_id,
            video_path,
            manifest_path,
            working,
            agents=list(TEXT_AGENT_CHAIN[:-1]),  # through quality
        )
        working.update(last_result.updated_state or {})

        manifest = {}
        try:
            from engines.ai_core.translation_agent.agent import load_manifest

            manifest = load_manifest(manifest_path)
        except Exception:
            manifest = {
                "source_lang": working.get("source_lang") or "en",
                "target_lang": working.get("target_lang") or "uk",
                "project_uuid": working.get("project_uuid") or "",
            }

        segments = list(working.get("segments") or [])
        segments, loop_log = run_reviewer_loop_for_segments(
            segments,
            manifest=manifest,
            state=working,
            task_id=task_id,
            source_lang=str(manifest.get("source_lang") or "en"),
            target_lang=str(manifest.get("target_lang") or "uk"),
        )
        working["segments"] = segments
        working["reviewer_loop_log"] = loop_log
        working["reviewer_agent_path"] = True

        try:
            write_reviewer_loop_json(
                task_id,
                loop_log,
                project_uuid=str(manifest.get("project_uuid") or ""),
            )
        except Exception as exc:
            logger.debug("reviewer_loop.json skipped: %s", exc)

        failed = [r for r in loop_log if not r.get("pass")]
        logger.info(
            "[ReviewerLoop] task=%s round=%d passed=%d failed=%d",
            task_id,
            round_num,
            len(loop_log) - len(failed),
            len(failed),
        )
        if not failed:
            break

    if last_result is None:
        return PipelineResult(status="partial", state=working)

    last_result.state = working
    return last_result
