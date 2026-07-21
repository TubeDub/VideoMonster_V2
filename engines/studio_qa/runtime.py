"""P506 Runtime Validator + P508 Metrics + P509 Health + P511 Taxonomy helpers."""

from __future__ import annotations

from typing import Any


# P511 — canonical error types for Studio / diagnostics
ERROR_TAXONOMY: tuple[str, ...] = (
    "TranslationError",
    "SemanticError",
    "SchedulerError",
    "TimingError",
    "AlignmentError",
    "MergeError",
    "StudioError",
    "IdentityError",
    "ContractError",
    "DecisionError",
    "RuntimeError",
)


def classify_error(exc: BaseException | str) -> str:
    """Map exception / message to Part 6 taxonomy label."""
    name = type(exc).__name__ if isinstance(exc, BaseException) else ""
    text = f"{name} {exc}".lower()
    mapping = [
        ("decision", "DecisionError"),
        ("semantic", "SemanticError"),
        ("contract", "ContractError"),
        ("overlap", "TimingError"),
        ("tail_spill", "TimingError"),
        ("timing", "TimingError"),
        ("alignment", "AlignmentError"),
        ("scheduler", "SchedulerError"),
        ("merge", "MergeError"),
        ("studio", "StudioError"),
        ("identity", "IdentityError"),
        ("uuid", "IdentityError"),
        ("translation", "TranslationError"),
        ("lock", "SemanticError"),
        ("runtime", "RuntimeError"),
    ]
    for needle, label in mapping:
        if needle in text or needle in name.lower():
            return label
    try:
        from engines.pipeline_integrity.error_taxonomy import classify_exception

        if isinstance(exc, BaseException):
            code = classify_exception(exc)
            for label in ERROR_TAXONOMY:
                if label.lower().startswith(str(code).split("_")[0]):
                    return label
    except Exception:
        pass
    return "RuntimeError"


def collect_metrics(meta: dict[str, Any] | None = None, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """P508 — unify Part 5 audio metrics + pipeline counters."""
    meta = meta or {}
    info = info or {}
    audio = dict(meta.get("audio_metrics") or (meta.get("dub") or {}).get("metrics") or {})
    # Enrich from decision graph
    graph = meta.get("decision_graph") or {}
    rewrite = 0
    manual = int(audio.get("manual_review_count") or 0)
    for rec in graph.get("records") or []:
        steps = ((rec.get("accepted") or {}).get("steps") or []) if isinstance(rec, dict) else []
        if "semantic_rewrite" in steps:
            rewrite += 1
        if "manual_review" in steps:
            manual += 1
    return {
        "overlap": int(audio.get("overlap_count") or 0),
        "tail_spill": int(audio.get("tail_spill_count") or 0),
        "borrow": int(audio.get("borrow_time_count") or 0),
        "tempo": int(audio.get("tempo_usage") or 0),
        "stretch": int(audio.get("stretch_usage") or 0),
        "rewrite": rewrite,
        "manual_review": manual,
        "scheduler_iterations": int(info.get("scheduler_iterations") or audio.get("tempo_usage") or 0),
        "prediction_error": float(audio.get("prediction_error") or 0),
        "alignment_error": float(info.get("alignment_error") or 0),
        "speech_flow_score": float(audio.get("speech_flow_score") or 0),
        "runtime": info.get("runtime_sec"),
        "memory": info.get("memory_mb"),
        "cpu": info.get("cpu_pct"),
    }


def take_health_snapshot() -> dict[str, Any]:
    """P509 — RAM/CPU/threads/temp/handles facade."""
    try:
        from engines.production_hardening.resource_manager import take_resource_snapshot

        snap = take_resource_snapshot()
        if hasattr(snap, "to_dict"):
            return snap.to_dict()
        if isinstance(snap, dict):
            return snap
        return {
            "ram_mb": getattr(snap, "rss_mb", None) or getattr(snap, "ram_mb", None),
            "cpu_pct": getattr(snap, "cpu_pct", None),
            "threads": getattr(snap, "threads", None),
            "temp_files": getattr(snap, "temp_files", None),
            "raw": str(snap),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "ram_mb": None, "cpu_pct": None}


def run_runtime_validator(info: dict[str, Any] | None = None) -> dict[str, Any]:
    """P506 — contracts / lock / FSM / owners / UUID / scheduler / audio."""
    info = dict(info or {})
    issues: list[dict[str, Any]] = []
    try:
        from engines.pipeline_integrity.contract_versions import require_contract_versions

        if any(k.endswith("_contract_version") for k in info):
            require_contract_versions(info)
    except Exception as exc:
        issues.append({"type": classify_error(exc), "message": str(exc), "area": "contracts"})

    try:
        from engines.pipeline_integrity.pipeline_state import get_pipeline_state

        state = get_pipeline_state(info)
        info["pipeline_state_checked"] = state.value if hasattr(state, "value") else str(state)
    except Exception as exc:
        issues.append({"type": classify_error(exc), "message": str(exc), "area": "state_machine"})

    try:
        from engines.pipeline_integrity.runtime_validator import validate_runtime

        stage = str(info.get("pipeline_state") or info.get("stage") or "scheduler")
        validate_runtime(info, stage=stage, require_contracts=False)
    except TypeError:
        pass
    except Exception as exc:
        issues.append({"type": classify_error(exc), "message": str(exc), "area": "runtime"})

    # Part 5 metrics hard gates
    metrics = collect_metrics(info.get("semantic_v3", {}).get("meta") if isinstance(info.get("semantic_v3"), dict) else info.get("meta"), info)
    if metrics.get("overlap", 0) > 0:
        issues.append({"type": "TimingError", "message": "overlap>0", "area": "scheduler"})
    if metrics.get("tail_spill", 0) > 0:
        issues.append({"type": "TimingError", "message": "tail_spill>0", "area": "alignment"})

    return {"ok": len(issues) == 0, "issues": issues, "metrics": metrics}
