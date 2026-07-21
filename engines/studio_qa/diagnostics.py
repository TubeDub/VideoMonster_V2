"""P507 Diagnostic Report ZIP + P510 Crash Recovery + P512 Observability."""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Any


def write_project_diagnostics_zip(
    out_path: Path | str,
    *,
    bundle: dict[str, Any],
    meta: dict[str, Any] | None = None,
    info: dict[str, Any] | None = None,
    logs: list[str] | None = None,
) -> Path:
    """
    P507 — project.diagnostics.zip with Pipeline, Timeline, Metrics,
    Decision Graph, Errors, Warnings, Logs, Contracts, Versions, Configuration.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = meta or {}
    info = info or {}

    payload = {
        "generated_at": time.time(),
        "pipeline": bundle.get("pipeline_view") or {},
        "timeline": bundle.get("timeline_view") or {},
        "metrics": bundle.get("metrics") or {},
        "decision_graph": bundle.get("decision_graph_view") or meta.get("decision_graph") or {},
        "errors": bundle.get("errors") or [],
        "warnings": bundle.get("warnings") or [],
        "logs": list(logs or []),
        "contracts": {
            k: info.get(k)
            for k in list(info.keys())
            if str(k).endswith("_contract_version")
        },
        "versions": {
            "studio_qa": "6.0",
            "pipeline_state": info.get("pipeline_state"),
            "semantic_phase": meta.get("phase") or info.get("phase"),
        },
        "configuration": {
            "profile": (meta.get("decision_graph") or {}).get("profile"),
            "dub_engine_v2": bool(meta.get("dub_engine_v2")),
            "bridge": meta.get("bridge"),
        },
        "replicas": bundle.get("replicas") or [],
        "review_panel": bundle.get("review_panel") or [],
        "acceptance": bundle.get("acceptance") or {},
        "health": bundle.get("health") or {},
    }

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.json", json.dumps(payload, ensure_ascii=False, indent=2))
        zf.writestr("pipeline.json", json.dumps(payload["pipeline"], ensure_ascii=False, indent=2))
        zf.writestr("timeline.json", json.dumps(payload["timeline"], ensure_ascii=False, indent=2))
        zf.writestr("metrics.json", json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
        zf.writestr(
            "decision_graph.json",
            json.dumps(payload["decision_graph"], ensure_ascii=False, indent=2),
        )
        zf.writestr("errors.json", json.dumps(payload["errors"], ensure_ascii=False, indent=2))
        if logs:
            zf.writestr("logs.txt", "\n".join(logs))
        zf.writestr(
            "contracts.json",
            json.dumps(payload["contracts"], ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "versions.json",
            json.dumps(payload["versions"], ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "configuration.json",
            json.dumps(payload["configuration"], ensure_ascii=False, indent=2),
        )
    return path


def save_crash_checkpoint(
    work_dir: Path | str,
    *,
    info: dict[str, Any],
    meta: dict[str, Any] | None = None,
    bundle: dict[str, Any] | None = None,
) -> Path:
    """P510 — persist state for resume (not full project restart)."""
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    enriched = dict(info)
    # Attach Part 6 resume payload without mutating caller's dict identity forever
    if meta:
        enriched.setdefault("studio_qa_meta_keys", list(meta.keys()))
        if meta.get("decision_graph") is not None:
            enriched["decision_graph"] = meta.get("decision_graph")
        if meta.get("speech_units") is not None:
            enriched["speech_units_checkpoint"] = meta.get("speech_units")
        if meta.get("timeline") is not None:
            enriched["timeline_checkpoint"] = meta.get("timeline")
    if bundle and bundle.get("acceptance") is not None:
        enriched["studio_qa_acceptance"] = bundle.get("acceptance")

    try:
        from engines.pipeline_integrity.crash_recovery import save_checkpoint

        return Path(save_checkpoint(root, enriched))
    except Exception:
        path = root / "studio_qa_checkpoint.json"
        path.write_text(
            json.dumps(
                {
                    "pipeline_state": info.get("pipeline_state"),
                    "task_id": info.get("task_id"),
                    "meta_keys": list((meta or {}).keys()),
                    "bundle_acceptance": (bundle or {}).get("acceptance"),
                    "decision_graph": (meta or {}).get("decision_graph"),
                    "speech_units": (meta or {}).get("speech_units"),
                    "timeline": (meta or {}).get("timeline"),
                    "contracts": {
                        k: info.get(k)
                        for k in info
                        if str(k).endswith("_contract_version")
                    },
                    "saved_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return path


def load_crash_checkpoint(work_dir: Path | str) -> dict[str, Any] | None:
    try:
        from engines.pipeline_integrity.crash_recovery import load_checkpoint

        cp = load_checkpoint(work_dir)
        if cp:
            return cp
    except Exception:
        pass
    path = Path(work_dir) / "studio_qa_checkpoint.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def observability_event(
    event: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """P512 — reproducible pipeline history event."""
    rec = {
        "event": event,
        "ts": time.time(),
        "details": details or {},
    }
    try:
        from engines.pipeline_integrity.observability import record_segment_event

        record_segment_event(event, **(details or {}))
    except Exception:
        pass
    return rec
