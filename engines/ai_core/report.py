"""AI Core — OpenDDF report.

Assembles the "AI Core Report" section: the full decision history for the run.
Per ТЗ it exposes, for every segment: the chosen strategy, how many variants
were created, the score of each variant, why the winner was chosen, LLM call
count, generation time, the model used, duration before/after adaptation, and
the final Slot Fit. It also surfaces the project-wide profile + strategy AI Core
decided up front.

All data is read from ``task_info`` (populated by the pipeline), so this builder
is pure and side-effect free.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.report")

_APP_DIR = Path(__file__).resolve().parents[2]
_OUTPUT_DIR = _APP_DIR / "output"


def _round(value: Any, ndigits: int = 3) -> float:
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return 0.0


def _segment_rows(task_info: dict[str, Any]) -> list[dict[str, Any]]:
    records = task_info.get("timing_aware_records") or []
    rows: list[dict[str, Any]] = []
    for rec in records:
        r = rec if isinstance(rec, dict) else getattr(rec, "to_dict", lambda: {})()
        trace = r.get("ai_adaptation_trace") or {}
        variants = trace.get("variants") or []
        variant_scores = [
            {
                "strategy": v.get("strategy", ""),
                "selected": bool(v.get("selected")),
                "rejected_reason": v.get("rejected_reason", ""),
                "scores": v.get("scores", {}),
            }
            for v in variants
        ]
        chosen_strategy = ""
        for v in variants:
            if v.get("selected"):
                chosen_strategy = v.get("strategy", "")
                break
        rows.append({
            "index": r.get("index"),
            "strategy": chosen_strategy or r.get("reason", ""),
            "variants_created": len(variants),
            "variant_scores": variant_scores,
            "winner_reason": trace.get("chosen_reason") or r.get("reason", ""),
            "llm_calls": int(trace.get("llm_calls") or 0),
            "generation_ms": _round(trace.get("llm_total_ms"), 1),
            "iterations": int(trace.get("iterations") or r.get("iterations") or 0),
            "duration_before_ms": r.get("predicted_ms_before"),
            "duration_after_ms": r.get("predicted_ms_after"),
            "slot_ms": r.get("slot_ms"),
            "slot_fit": _round(trace.get("slot_fit_score")),
            "meaning_score": _round(trace.get("meaning_score")),
            "naturalness_score": _round(trace.get("naturalness_score")),
            "llm_called": bool(r.get("llm_called")),
            "requires_llm_adaptation": bool(r.get("requires_llm_adaptation")),
        })
    return rows


def build_ai_core_report(task_id_or_info: str | dict[str, Any]) -> dict[str, Any]:
    """Build AI Core report from task_id or legacy task_info dict."""
    if isinstance(task_id_or_info, dict):
        return _build_ai_core_report_from_task_info(task_id_or_info)
    return _build_ai_core_report_from_task_id(str(task_id_or_info))


def _build_ai_core_report_from_task_id(task_id: str) -> dict[str, Any]:
    """Aggregate OpenDDF entries, LLM calls, timing, fallbacks, final status."""
    from engines.open_ddf import open_ddf

    ddf = open_ddf.load(task_id) or open_ddf.get_report(task_id)
    agents = list(ddf.get("agents") or [])
    summary = dict(ddf.get("summary") or {})

    llm_calls: list[dict] = []
    llm_status: dict = {}
    try:
        from engines.ai_core import llm_gateway

        llm_calls = llm_gateway.calls()
        llm_status = llm_gateway.status()
    except Exception:
        pass

    fallbacks = [
        {
            "agent": a.get("agent_name"),
            "reason": a.get("fallback_reason") or a.get("decision"),
            "segment": a.get("segment_idx"),
        }
        for a in agents
        if a.get("fallback_used")
    ]

    total_llm_from_agents = sum(int(a.get("llm_calls") or 0) for a in agents)
    total_exec_from_agents = sum(float(a.get("execution_time_ms") or 0.0) for a in agents)

    per_agent: dict[str, dict] = {}
    for entry in agents:
        name = str(entry.get("agent_name") or "unknown")
        per_agent[name] = {
            "called": entry.get("called"),
            "success": entry.get("success"),
            "execution_time_ms": entry.get("execution_time_ms"),
            "retry_count": entry.get("retry_count"),
            "fallback_used": entry.get("fallback_used"),
            "fallback_reason": entry.get("fallback_reason"),
            "llm_calls": entry.get("llm_calls"),
            "decision": entry.get("decision"),
            "input_metrics": entry.get("input_metrics"),
            "output_metrics": entry.get("output_metrics"),
        }

    report = {
        "task_id": task_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "final_status": "completed" if not summary.get("failed_agents") else "partial",
        "ddf_summary": summary,
        "agents": per_agent,
        "agent_timeline": agents,
        "llm_calls_count": len(llm_calls),
        "llm_calls": llm_calls[:200],
        "llm_status": llm_status,
        "fallbacks": fallbacks,
        "segment_attention": list(ddf.get("segment_attention") or []),
        "totals": {
            "agents_run": len(agents),
            "failed_agents": int(summary.get("failed_agents") or 0),
            "fallback_count": int(summary.get("fallback_used") or 0),
            "total_llm_calls": int(
                summary.get("total_llm_calls") or total_llm_from_agents or len(llm_calls)
            ),
            "total_execution_ms": float(
                summary.get("total_execution_ms") or total_exec_from_agents or 0.0
            ),
        },
    }
    return report


def save_ai_core_report(
    task_id: str,
    *,
    task_info: dict[str, Any] | None = None,
) -> Path | None:
    """Persist ``output/ai_core_report_{task_id}.json``."""
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report = _build_ai_core_report_from_task_id(task_id)
        if task_info:
            report["legacy_ai_core"] = _build_ai_core_report_from_task_info(task_info)
        path = _OUTPUT_DIR / f"ai_core_report_{task_id}.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        logger.info("[AI Core Report] saved %s", path)
        return path
    except Exception as exc:
        logger.warning("[AI Core Report] save failed for %s: %s", task_id, exc)
        return None


def load_ai_core_report(task_id: str) -> dict[str, Any] | None:
    path = _OUTPUT_DIR / f"ai_core_report_{task_id}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _build_ai_core_report_from_task_info(task_info: dict[str, Any]) -> dict[str, Any]:
    """Build the OpenDDF "AI Core Report" from ``task_info``."""
    ai_core = task_info.get("ai_core") or {}
    profile = ai_core.get("profile") or {}
    strategy = ai_core.get("strategy") or {}

    rows = _segment_rows(task_info)

    total_variants = sum(r["variants_created"] for r in rows)
    total_llm_calls = sum(r["llm_calls"] for r in rows)
    total_gen_ms = sum(r["generation_ms"] for r in rows)
    seg_n = len(rows) or 1
    slot_fits = [r["slot_fit"] for r in rows if r["slot_fit"]]
    meanings = [r["meaning_score"] for r in rows if r["meaning_score"]]

    model = strategy.get("model") or ""
    if not model:
        model = ((task_info.get("llm_status") or {}).get("model")) or ""

    summary = {
        "enabled": bool(ai_core),
        "model": model,
        "segment_count": len(rows),
        "avg_variants_per_segment": _round(total_variants / seg_n, 2),
        "total_variants": total_variants,
        "total_llm_calls": total_llm_calls,
        "total_generation_ms": _round(total_gen_ms, 1),
        "avg_slot_fit": _round(sum(slot_fits) / len(slot_fits), 3) if slot_fits else 0.0,
        "avg_meaning_score": _round(sum(meanings) / len(meanings), 3) if meanings else 0.0,
    }

    # Human-readable Russian labels matching the ТЗ wording.
    report_ru = {
        "Единая точка решений (AI Core)": bool(ai_core),
        "Тип контента": profile.get("content_type", ""),
        "Жанр": profile.get("genre", ""),
        "Стиль речи": profile.get("speech_style", ""),
        "Доминирующая эмоция": profile.get("dominant_emotion", ""),
        "Темп речи": profile.get("tempo", ""),
        "Сложность текста": profile.get("complexity", ""),
        "Количество спикеров": profile.get("speaker_count", ""),
        "Режим (скорость/качество)": strategy.get("speed_mode", ""),
        "Диапазон вариантов": f"{strategy.get('min_variants', '')}–{strategy.get('max_variants', '')}",
        "Использованная модель": model,
        "Среднее число вариантов": summary["avg_variants_per_segment"],
        "Всего вызовов LLM": total_llm_calls,
        "Средний Slot Fit": summary["avg_slot_fit"],
        "Средний Meaning Score": summary["avg_meaning_score"],
    }

    return {
        "profile": profile,
        "strategy": strategy,
        "summary": summary,
        "segments": rows,
        "timeline": build_ai_core_timeline(task_info),
        "ai_agent_report": build_ai_agent_report(task_info),
        "ai_agent_timeline": build_ai_agent_timeline(task_info),
        "performance": build_ai_performance_report(task_info),
        "ai_core_report_ru": report_ru,
        "decision_rationale": strategy.get("rationale", []),
    }


# Canonical agent order for the "AI Agent Timeline" section (AI Core 3.0).
_AGENT_CHAIN = [
    "planner", "translation", "semantic", "entity", "timing",
    "grammar", "quality", "voice", "mix",
]


def build_ai_agent_report(task_info: dict[str, Any]) -> dict[str, Any]:
    """OpenDDF "AI Agent Report": per-agent call/decision/error summary."""
    ai_core = task_info.get("ai_core") or {}
    planner = dict((ai_core.get("agent_report") or {}).get("planner") or {})
    mix = dict((ai_core.get("agent_report") or {}).get("mix") or {})
    timeline = build_ai_agent_timeline(task_info)
    per_agent = dict(timeline.get("per_agent") or {})
    segments = list(timeline.get("segments") or [])

    agents: dict[str, dict[str, Any]] = {}
    agents["planner"] = {
        "called": bool(planner.get("called")),
        "execution_time_ms": _round(planner.get("execution_time_ms"), 1),
        "input_data": planner.get("input_data") or {},
        "output_data": planner.get("output_data") or {},
        "decision_taken": planner.get("decision_taken") or "",
        "errors": list(planner.get("errors") or []),
        "rerun": bool(planner.get("rerun")),
        "status": planner.get("status") or ("called" if planner.get("called") else "not_called"),
    }
    agents["mix"] = {
        "called": bool(mix.get("called")),
        "execution_time_ms": _round(mix.get("execution_time_ms"), 1),
        "input_data": mix.get("input_data") or {},
        "output_data": mix.get("output_data") or {},
        "decision_taken": mix.get("decision_taken") or "",
        "errors": list(mix.get("errors") or []),
        "rerun": bool(mix.get("rerun")),
        "status": mix.get("status") or ("called" if mix.get("called") else "not_called"),
    }

    for agent in _AGENT_CHAIN[1:]:
        if agent == "mix":
            continue
        agg = per_agent.get(agent) or {}
        steps = []
        for seg in segments:
            for step in seg.get("chain") or []:
                if step.get("agent") == agent:
                    steps.append({
                        "segment": seg.get("index"),
                        "input_data": step.get("input_data"),
                        "output_data": step.get("output_data"),
                        "decision_taken": step.get("reason", ""),
                        "errors": step.get("diagnostics", {}).get("errors") if isinstance(step.get("diagnostics"), dict) else None,
                        "rerun": bool(step.get("attempts", 1) > 1),
                    })
        agents[agent] = {
            "called": bool(agg) or bool(steps),
            "execution_time_ms": _round(agg.get("total_time_ms"), 1),
            "input_data": steps[0]["input_data"] if steps else None,
            "output_data": steps[0]["output_data"] if steps else None,
            "decision_taken": steps[0]["decision_taken"] if steps else "",
            "errors": [e for s in steps for e in ([s["errors"]] if isinstance(s["errors"], str) else (s["errors"] or []))],
            "rerun": any(s["rerun"] for s in steps),
            "status": "called" if bool(agg) or bool(steps) else "not_called",
            "runs": int(agg.get("runs") or 0),
            "changes": int(agg.get("changes") or 0),
            "llm_calls": int(agg.get("llm_calls") or 0),
            "cache_hits": int(agg.get("cache_hits") or 0),
        }

    return {
        "enabled": bool(ai_core) or timeline.get("enabled", False),
        "agent_order": _AGENT_CHAIN,
        "agents": agents,
    }


def build_ai_agent_timeline(task_info: dict[str, Any]) -> dict[str, Any]:
    """OpenDDF "AI Agent Timeline" (AI Core 3.0).

    For every segment shows the agent chain
    Planner → Translation → Semantic → Entities → Timing → Grammar → Quality →
    Voice → Mix, and per agent: time taken, model used, attempts, reason for
    changes and quality score. Data is read from each record's
    ``ai_adaptation_trace["agent_timeline"]`` (stamped by the coordinator), so
    this builder is pure and never raises.
    """
    records = task_info.get("timing_aware_records") or []
    segments: list[dict[str, Any]] = []
    agent_totals: dict[str, dict[str, float]] = {}

    for rec in records:
        r = rec if isinstance(rec, dict) else getattr(rec, "to_dict", lambda: {})()
        trace = r.get("ai_adaptation_trace") or {}
        chain = trace.get("agent_timeline") or []
        seg_row = {
            "index": r.get("index"),
            "chain": chain,
            "quality_score": _round(trace.get("agent_quality_score")),
            "quality_pass": bool(trace.get("agent_quality_pass", True)),
            "final_text": (r.get("text_after") or "")[:200],
            "voice": trace.get("voice") or {},
            "watchdog_fallback": bool(trace.get("watchdog_fallback")),
        }
        segments.append(seg_row)
        for step in chain:
            name = step.get("agent", "?")
            agg = agent_totals.setdefault(
                name, {"time_ms": 0.0, "runs": 0, "changes": 0, "llm": 0, "cache_hits": 0}
            )
            agg["time_ms"] += float(step.get("time_ms") or 0.0)
            agg["runs"] += 1 if not step.get("skipped") else 0
            agg["changes"] += 1 if step.get("changed") else 0
            agg["llm"] += 1 if step.get("used_llm") else 0
            agg["cache_hits"] += 1 if step.get("cache_hit") else 0

    per_agent = {
        name: {
            "total_time_ms": _round(v["time_ms"], 1),
            "runs": int(v["runs"]),
            "changes": int(v["changes"]),
            "llm_calls": int(v["llm"]),
            "cache_hits": int(v["cache_hits"]),
        }
        for name, v in agent_totals.items()
    }

    enabled = any(seg["chain"] for seg in segments)
    return {
        "enabled": enabled,
        "agent_order": _AGENT_CHAIN,
        "segment_count": len(segments),
        "segments": segments,
        "per_agent": per_agent,
        "ai_agent_timeline_ru": {
            "Мультиагентный режим": enabled,
            "Порядок агентов": " → ".join(_AGENT_CHAIN),
            "Сегментов": len(segments),
        },
    }


def build_ai_core_timeline(task_info: dict[str, Any]) -> list[dict[str, Any]]:
    """OpenDDF "AI Core Timeline" (Task 12): per-segment processing span.

    Each row: start/end time, strategy, attempts, variants, chosen variant,
    final duration, final Slot Fit, and any error reason.
    """
    records = task_info.get("timing_aware_records") or []
    timeline: list[dict[str, Any]] = []
    for rec in records:
        r = rec if isinstance(rec, dict) else getattr(rec, "to_dict", lambda: {})()
        trace = r.get("ai_adaptation_trace") or {}
        variants = trace.get("variants") or []
        chosen = ""
        for v in variants:
            if v.get("selected"):
                chosen = v.get("text", "") or v.get("strategy", "")
                break
        timeline.append({
            "index": r.get("index"),
            "started_at": trace.get("started_at"),
            "ended_at": trace.get("ended_at"),
            "duration_ms": _round(trace.get("total_ms"), 1),
            "strategy": trace.get("strategy_class") or r.get("reason", ""),
            "attempts": int(trace.get("attempts") or trace.get("iterations") or 0),
            "variants": len(variants),
            "chosen_variant": (chosen or trace.get("chosen_text") or "")[:200],
            "final_duration_ms": r.get("predicted_ms_after"),
            "slot_ms": r.get("slot_ms"),
            "slot_fit": _round(trace.get("slot_fit_score")),
            "error": trace.get("error") or r.get("llm_skip_reason") or "",
        })
    return timeline


def build_ai_performance_report(task_info: dict[str, Any]) -> dict[str, Any]:
    """AI Performance Report (Task 11): aggregate timing + strategy breakdown."""
    records = task_info.get("timing_aware_records") or []
    rows = [r if isinstance(r, dict) else getattr(r, "to_dict", lambda: {})() for r in records]
    n = len(rows) or 1

    total_llm_calls = 0
    total_llm_ms = 0.0
    total_seg_ms = 0.0
    retries = 0
    needed_llm = 0
    strat_counts = {"none": 0, "rule_rewrite": 0, "full_llm": 0, "other": 0}
    for r in rows:
        trace = r.get("ai_adaptation_trace") or {}
        total_llm_calls += int(trace.get("llm_calls") or 0)
        total_llm_ms += float(trace.get("llm_total_ms") or 0.0)
        total_seg_ms += float(trace.get("total_ms") or 0.0)
        attempts = int(trace.get("attempts") or trace.get("iterations") or 0)
        if attempts > 1:
            retries += attempts - 1
        sclass = str(trace.get("strategy_class") or "")
        if sclass in strat_counts:
            strat_counts[sclass] += 1
        else:
            strat_counts["other"] += 1
        if bool(r.get("llm_called")):
            needed_llm += 1

    seg_n = len(rows)
    fast_segments = strat_counts["none"] + strat_counts["rule_rewrite"]
    llm_segments = strat_counts["full_llm"]

    perf = {
        "segment_count": seg_n,
        "total_ai_core_ms": _round(total_seg_ms, 1),
        "total_llm_ms": _round(total_llm_ms, 1),
        "avg_segment_ms": _round(total_seg_ms / n, 1),
        "total_llm_calls": total_llm_calls,
        "total_retries": retries,
        "segments_needed_llm": needed_llm,
        "fast_path_pct": _round(100.0 * fast_segments / n, 1),
        "full_llm_pct": _round(100.0 * llm_segments / n, 1),
        "strategy_breakdown": strat_counts,
    }
    perf["ai_performance_report_ru"] = {
        "Всего сегментов": seg_n,
        "Время работы AI Core (мс)": perf["total_ai_core_ms"],
        "Время работы LLM (мс)": perf["total_llm_ms"],
        "Среднее время сегмента (мс)": perf["avg_segment_ms"],
        "Вызовов LLM": total_llm_calls,
        "Повторных попыток": retries,
        "Сегментов с реальной адаптацией LLM": needed_llm,
        "% быстрым режимом": perf["fast_path_pct"],
        "% полным LLM Rewrite": perf["full_llm_pct"],
    }
    return perf


def build_and_save_ai_core_report(
    task_id: str,
    *,
    task_info: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> Path | None:
    """Alias for orchestrator / auto_dub_api — persist AI Core report JSON."""
    if state and not task_info:
        task_info = {"orchestrator_state": state}
    return save_ai_core_report(task_id, task_info=task_info)


def build_ai_core_report_for_task(
    task_id: str,
    *,
    task_info: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate OpenDDF + LLM telemetry into a single AI Core report dict."""
    from engines.open_ddf import open_ddf

    ddf = open_ddf.load(task_id) or open_ddf.get_report(task_id)
    info = dict(task_info or {})
    if state:
        info.setdefault("orchestrator_state", state)

    legacy = build_ai_core_report(info) if info else {}

    agents = list(ddf.get("agents") or [])
    agent_timing: dict[str, float] = {}
    agent_fallbacks: list[dict[str, Any]] = []
    for row in agents:
        name = str(row.get("agent_name") or "?")
        agent_timing[name] = round(
            float(agent_timing.get(name) or 0) + float(row.get("execution_time_ms") or 0),
            1,
        )
        if row.get("fallback_used"):
            agent_fallbacks.append(
                {
                    "agent": name,
                    "reason": row.get("fallback_reason") or row.get("decision"),
                    "segment_idx": row.get("segment_idx"),
                }
            )

    llm_status: dict[str, Any] = {}
    llm_calls_n = 0
    try:
        from engines.ai_core import llm_gateway

        llm_status = llm_gateway.status()
        llm_calls_n = len(llm_gateway.calls())
    except Exception:
        pass

    summary = dict(ddf.get("summary") or {})
    final_status = "success"
    if summary.get("failed_agents"):
        final_status = "warning"
    if ddf.get("error") == "no_data" and not agents:
        final_status = "no_data"

    return {
        "task_id": task_id,
        "final_status": final_status,
        "ddf_summary": summary,
        "agents": agents,
        "agent_timing_ms": agent_timing,
        "fallbacks": agent_fallbacks,
        "llm_status": llm_status,
        "llm_calls_count": llm_calls_n,
        "llm_decisions": llm_status.get("recent_decisions") or [],
        "segment_attention": list(ddf.get("segment_attention") or []),
        "legacy_ai_core": legacy,
        "saved_at": ddf.get("saved_at"),
    }
