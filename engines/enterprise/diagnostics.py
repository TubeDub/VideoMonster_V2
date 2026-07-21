"""P811 Self Diagnostics + P812 Failure Recovery + P813 Observability."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from engines.enterprise.pipeline_versions import collect_pipeline_versions


def run_self_diagnostics(*, app_dir: Path | str | None = None) -> dict[str, Any]:
    """P811 — pre-flight: contracts, plugins, TTS, config, cache, paths, versions."""
    root = Path(app_dir or Path(__file__).resolve().parents[2])
    checks: list[dict[str, Any]] = []

    # Contracts
    try:
        from engines.pipeline_integrity.contract_versions import CONTRACT_VERSIONS

        checks.append({"name": "contracts", "ok": bool(CONTRACT_VERSIONS), "data": dict(CONTRACT_VERSIONS)})
    except Exception as exc:
        checks.append({"name": "contracts", "ok": False, "error": str(exc)})

    # Plugins
    try:
        from engines.platform_sdk import get_public_api

        points = get_public_api().list_extension_points()
        checks.append({"name": "plugins", "ok": len(points) >= 5, "extension_points": points})
    except Exception as exc:
        checks.append({"name": "plugins", "ok": False, "error": str(exc)})

    # TTS / Voice Platform
    try:
        from engines.voice_platform import list_providers

        providers = list_providers()
        checks.append({"name": "tts", "ok": len(providers) >= 1, "providers": len(providers)})
    except Exception as exc:
        checks.append({"name": "tts", "ok": False, "error": str(exc)})

    # Configuration
    try:
        from engines.enterprise.configuration import get_config_store

        store = get_config_store()
        checks.append({"name": "configuration", "ok": len(store.list_domains()) >= 5})
    except Exception as exc:
        checks.append({"name": "configuration", "ok": False, "error": str(exc)})

    # Cache / paths
    for name, path in (
        ("output_path", root / "output"),
        ("data_path", root / "data"),
        ("voice_cache", root / "output" / "voice_cache"),
    ):
        checks.append({"name": name, "ok": path.exists() or path.parent.exists(), "path": str(path)})

    # Versions
    versions = collect_pipeline_versions().to_dict()
    checks.append({"name": "versions", "ok": True, "data": versions})

    ok = all(c.get("ok") for c in checks)
    return {"ok": ok, "checks": checks, "ts": time.time()}


def save_failure_checkpoint(
    work_dir: Path | str,
    *,
    info: dict[str, Any],
    meta: dict[str, Any] | None = None,
    task_graph: dict[str, Any] | None = None,
) -> Path:
    """P812 — persist Pipeline, Decision Graph, Timeline, Task Graph."""
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    meta = meta or {}
    payload = {
        "saved_at": time.time(),
        "pipeline_state": info.get("pipeline_state"),
        "decision_graph": meta.get("decision_graph") or info.get("decision_graph"),
        "timeline": meta.get("timeline") or info.get("timeline"),
        "speech_units": meta.get("speech_units"),
        "task_graph": task_graph,
        "pipeline_version_bundle": info.get("pipeline_version_bundle")
        or meta.get("pipeline_version_bundle"),
    }
    path = root / "enterprise_failure_checkpoint.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    try:
        from engines.pipeline_integrity.crash_recovery import save_checkpoint

        save_checkpoint(root, info)
    except Exception:
        pass
    try:
        from engines.studio_qa.diagnostics import save_crash_checkpoint

        save_crash_checkpoint(root, info=info, meta=meta)
    except Exception:
        pass
    return path


def load_failure_checkpoint(work_dir: Path | str) -> dict[str, Any] | None:
    path = Path(work_dir) / "enterprise_failure_checkpoint.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        from engines.pipeline_integrity.crash_recovery import load_checkpoint

        return load_checkpoint(work_dir)
    except Exception:
        return None


class ObservabilityPlatform:
    """P813 — metrics, events, errors, decisions, performance, history."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.metrics: dict[str, Any] = {}

    def record_event(self, name: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append({"event": name, "ts": time.time(), "payload": payload or {}})
        try:
            from engines.platform_sdk.event_bus import get_platform_bus

            get_platform_bus().publish(name, payload or {})
        except Exception:
            pass

    def record_error(self, error: str, *, area: str = "") -> None:
        self.errors.append({"error": error, "area": area, "ts": time.time()})

    def record_decision(self, decision: dict[str, Any]) -> None:
        self.decisions.append(decision)

    def set_metrics(self, metrics: dict[str, Any]) -> None:
        self.metrics.update(metrics)

    def export(self) -> dict[str, Any]:
        return {
            "events": list(self.events),
            "errors": list(self.errors),
            "decisions": list(self.decisions),
            "metrics": dict(self.metrics),
            "history_len": len(self.events),
        }
