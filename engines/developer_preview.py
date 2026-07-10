"""Developer Preview — incremental dub preview, timing stats, agent timeline (debug only)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.developer_preview")

MIN_PREVIEW_SEGMENTS = 2
PREVIEW_DEBOUNCE_SEC = 2.5

AGENT_ORDER = (
    "extract",
    "whisper",
    "translation",
    "semantic",
    "grammar",
    "timing",
    "tts",
    "slot_fit",
    "mix",
    "mux",
    "export",
)

AGENT_LABELS = {
    "extract": "Extract",
    "whisper": "Whisper",
    "translation": "Translation",
    "semantic": "Semantic",
    "grammar": "Grammar",
    "timing": "Timing",
    "tts": "TTS",
    "slot_fit": "Slot Fit",
    "mix": "Mix",
    "mux": "Mux",
    "export": "Export",
}

_PREVIEW_LOCK = threading.Lock()
_PREVIEW_SCHEDULED: dict[str, float] = {}


def count_tts_ready_segments(segments_data: list[dict]) -> int:
    n = 0
    for seg in segments_data or []:
        if seg.get("merged_into") is not None:
            continue
        if seg.get("tts_file_path") or seg.get("file"):
            n += 1
    return n


def contiguous_ready_prefix(segments_data: list[dict]) -> int:
    """Last index (inclusive) with contiguous TTS from segment 0."""
    last = -1
    for idx, seg in enumerate(segments_data or []):
        if seg.get("merged_into") is not None:
            continue
        if seg.get("tts_file_path") or seg.get("file"):
            last = idx
        else:
            break
    return last


def detect_first_pipeline_error(task_info: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("pipeline_error", "last_tts_error", "last_pipeline_diagnostic"):
        val = task_info.get(key)
        if isinstance(val, dict):
            return {
                "source": key,
                "code": val.get("code") or val.get("error_code"),
                "message": val.get("reason_short") or val.get("reason") or val.get("message"),
            }
        if isinstance(val, str) and val.strip():
            return {"source": key, "message": val.strip()}
    for fail in task_info.get("tts_failures") or []:
        if isinstance(fail, dict) and fail.get("error"):
            return {"source": "tts_failures", "message": str(fail.get("error"))[:300]}
    gate = (task_info.get("post_tts_qa") or {}).get("requires_llm_adaptation")
    if isinstance(gate, dict) and gate.get("count"):
        return {
            "source": "post_tts_qa",
            "code": "REQUIRES_LLM_ADAPTATION",
            "message": str(gate.get("reason") or "Segments require LLM adaptation"),
        }
    return None


def compute_timing_stats(
    task_info: dict[str, Any],
    *,
    progress: float = 0.0,
    step: str = "",
) -> dict[str, Any]:
    segments_data = task_info.get("segments_data") or []
    total = sum(1 for s in segments_data if s.get("merged_into") is None)
    ready = count_tts_ready_segments(segments_data)
    started = float(task_info.get("pipeline_started_at") or task_info.get("step_started_at") or 0)
    elapsed_sec = int(max(0, time.time() - started)) if started > 0 else 0

    detail = task_info.get("progress_detail") or {}
    eta_sec = detail.get("eta_sec")
    if eta_sec is None and progress > 0.02 and elapsed_sec > 0:
        eta_sec = int(elapsed_sec * (100.0 - progress) / max(progress, 0.01))

    avg_segment_ms = None
    tts_started = float(task_info.get("tts_stage_started_at") or 0)
    if ready > 0 and tts_started > 0:
        avg_segment_ms = int((time.time() - tts_started) * 1000 / ready)

    return {
        "elapsed_sec": elapsed_sec,
        "eta_sec": eta_sec,
        "avg_segment_ms": avg_segment_ms,
        "segments_ready": ready,
        "segments_total": total,
        "step": step,
        "progress_pct": round(float(progress), 1),
    }


def record_agent_event(
    task_info: dict[str, Any],
    agent: str,
    event: str,
    *,
    duration_ms: int | None = None,
    detail: str = "",
) -> None:
    timeline = task_info.setdefault("developer_timeline", [])
    now = time.time()
    entry = {
        "agent": agent,
        "event": event,
        "ts": now,
        "duration_ms": duration_ms,
        "detail": detail[:200] if detail else "",
    }
    if timeline and timeline[-1].get("agent") == agent and timeline[-1].get("event") == "running":
        if event in ("done", "failed", "skipped"):
            started = float(timeline[-1].get("ts") or now)
            entry["duration_ms"] = duration_ms if duration_ms is not None else int((now - started) * 1000)
    timeline.append(entry)
    if len(timeline) > 200:
        del timeline[:-200]
    task_info["developer_timeline_active"] = agent if event == "running" else None


def build_agent_timeline_view(task_info: dict[str, Any], *, current_step: str = "") -> list[dict[str, Any]]:
    """Horizontal timeline: pending | running | done per agent."""
    events = list(task_info.get("developer_timeline") or [])
    state: dict[str, str] = {a: "pending" for a in AGENT_ORDER}
    durations: dict[str, int] = {}
    for ev in events:
        agent = str(ev.get("agent") or "")
        if agent not in state:
            continue
        evt = str(ev.get("event") or "")
        if evt == "running":
            state[agent] = "running"
        elif evt == "done":
            state[agent] = "done"
            if ev.get("duration_ms"):
                durations[agent] = int(ev.get("duration_ms") or 0)
        elif evt == "failed":
            state[agent] = "failed"
        elif evt == "skipped":
            state[agent] = "skipped"

    step_map = {
        "preparing": "extract",
        "extract_audio": "extract",
        "transcribe": "whisper",
        "translate": "translation",
        "tts": "tts",
        "timing": "timing",
        "dub": "mux",
        "studio": "mix",
        "done": "export",
    }
    active = step_map.get(current_step or "", task_info.get("developer_timeline_active") or "")
    if active and state.get(active) == "pending":
        state[active] = "running"

    rows = []
    for agent in AGENT_ORDER:
        rows.append(
            {
                "agent": agent,
                "label": AGENT_LABELS.get(agent, agent),
                "status": state.get(agent, "pending"),
                "duration_ms": durations.get(agent, 0),
            }
        )
    return rows


def build_performance_table(task_info: dict[str, Any]) -> list[dict[str, Any]]:
    timing = task_info.get("pipeline_timing") or {}
    stages = timing.get("stages") or timing.get("seconds") or {}
    rows: list[dict[str, Any]] = []
    if isinstance(stages, dict):
        for key in AGENT_ORDER:
            sec = stages.get(key)
            if sec is None and key == "whisper":
                sec = stages.get("whisper")
            if sec is None:
                continue
            try:
                val = float(sec)
            except (TypeError, ValueError):
                continue
            if val <= 0:
                continue
            rows.append({"agent": key, "label": AGENT_LABELS.get(key, key), "duration_sec": round(val, 2)})
    llm_calls = task_info.get("llm_calls") or []
    llm_ms = sum(float(c.get("ms") or 0) for c in llm_calls)
    if llm_ms > 0:
        rows.append({"agent": "llm", "label": "LLM", "duration_sec": round(llm_ms / 1000.0, 2)})
    return rows


def build_performance_report_summary(task_info: dict[str, Any]) -> dict[str, Any]:
    table = build_performance_table(task_info)
    if not table:
        return {"agents": [], "slowest": None, "total_sec": 0.0}
    slowest = max(table, key=lambda r: r.get("duration_sec") or 0)
    total = sum(float(r.get("duration_sec") or 0) for r in table)
    llm_calls = len(task_info.get("llm_calls") or [])
    return {
        "agents": table,
        "slowest": slowest,
        "total_sec": round(total, 2),
        "llm_call_count": llm_calls,
        "report_path": task_info.get("performance_report"),
    }


def resolve_restart_cache_plan(
    info: dict[str, Any],
    changes: dict[str, Any],
    *,
    checkpoint: str,
) -> dict[str, Any]:
    """Which stages to skip on restart (voice-only → TTS→Mix→MP4)."""
    old_lang = str(info.get("target_lang") or "")
    old_voice = str(info.get("voice") or info.get("tts_voice") or "")
    old_style = str(info.get("dub_style") or "")
    new_lang = str(changes.get("target_lang") or old_lang)
    new_voice = str(changes.get("voice") or old_voice)
    new_style = str(changes.get("dub_style") or old_style)

    plan = {
        "checkpoint": checkpoint,
        "skip_translate": False,
        "skip_semantic": False,
        "skip_grammar": False,
        "skip_timing_adapt": False,
        "skip_tts": False,
        "reason": "full_pipeline",
    }
    if checkpoint in ("start", "post_stt"):
        return plan
    if new_lang and new_lang != old_lang:
        plan["reason"] = "language_changed"
        return plan
    if checkpoint in ("post_translation", "post_ai_core_text", "post_voice", "post_mix"):
        plan["skip_translate"] = True
        plan["skip_semantic"] = True
        plan["skip_grammar"] = True
        plan["skip_timing_adapt"] = True
        if new_voice and new_voice != old_voice and new_style == old_style:
            plan["reason"] = "voice_only"
        elif new_style != old_style:
            plan["reason"] = "style_changed_tts_only"
        else:
            plan["reason"] = "cached_text_tts_rerun"
        if checkpoint in ("post_voice", "post_mix"):
            plan["skip_tts"] = False
    return plan


def build_status_payload(task_info: dict[str, Any], *, step: str = "", progress: float = 0.0) -> dict[str, Any]:
    preview = dict(task_info.get("developer_preview") or {})
    return {
        "preview": preview,
        "timing": compute_timing_stats(task_info, progress=progress, step=step),
        "timeline": build_agent_timeline_view(task_info, current_step=step),
        "performance": build_performance_table(task_info),
        "performance_summary": build_performance_report_summary(task_info),
        "first_error": detect_first_pipeline_error(task_info),
        "cache_plan": task_info.get("restart_cache_plan"),
    }


def schedule_preview_build(
    task_id: str,
    build_fn: Callable[[], None],
    *,
    debounce_sec: float = PREVIEW_DEBOUNCE_SEC,
) -> None:
    """Debounced background preview rebuild."""
    now = time.time()
    with _PREVIEW_LOCK:
        _PREVIEW_SCHEDULED[task_id] = now

    def _runner() -> None:
        time.sleep(debounce_sec)
        with _PREVIEW_LOCK:
            if _PREVIEW_SCHEDULED.get(task_id) != now:
                return
        try:
            build_fn()
        except Exception as exc:
            logger.warning("[DevPreview] build failed task=%s: %s", task_id, exc)

    threading.Thread(target=_runner, daemon=True, name=f"dev-preview-{task_id[:8]}").start()
