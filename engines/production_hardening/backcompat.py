"""P16.4 — Backward compatibility checks."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.contract_versions import (
    CONTRACT_VERSIONS,
    stamp_contract_versions,
)
from engines.pipeline_integrity.pipeline_state import PipelineState, parse_pipeline_state


def read_legacy_openddf(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("openddf root must be object")
    # Minimal required keys for legacy readers
    data.setdefault("segments", data.get("segments") or [])
    data.setdefault("summary", data.get("summary") or {})
    return data


def read_legacy_diagnostic_zip(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"files": [], "json": {}}
    with zipfile.ZipFile(path, "r") as zf:
        out["files"] = zf.namelist()
        for name in zf.namelist():
            if name.endswith(".json"):
                try:
                    out["json"][name] = json.loads(zf.read(name).decode("utf-8"))
                except Exception as exc:
                    out["json"][name] = {"error": str(exc)}
    return out


def migrate_project_info(info: dict[str, Any]) -> dict[str, Any]:
    """Upgrade older project meta without breaking locked text."""
    # Old FSM without HANDOFF: MERGED is still valid
    state = str(info.get("pipeline_state") or "NEW")
    try:
        parse_pipeline_state(state)
    except Exception:
        info["pipeline_state"] = PipelineState.NEW.value
    # Stamp missing contract versions (do not overwrite mismatched majors blindly)
    for key, ver in CONTRACT_VERSIONS.items():
        if info.get(key) is None:
            info[key] = ver
    try:
        stamp_contract_versions(info)
    except Exception:
        # Keep existing mismatched versions visible; caller handles ContractVersionError
        pass
    return info


def check_backward_compatibility(sample_paths: list[Path]) -> dict[str, Any]:
    results = []
    for path in sample_paths:
        entry = {"path": str(path), "ok": False, "kind": ""}
        try:
            if path.suffix.lower() == ".zip":
                entry["kind"] = "diagnostic_zip"
                read_legacy_diagnostic_zip(path)
            else:
                entry["kind"] = "openddf_json"
                read_legacy_openddf(path)
            entry["ok"] = True
        except Exception as exc:
            entry["error"] = str(exc)
        results.append(entry)
    return {
        "ok": all(r["ok"] for r in results) if results else True,
        "checked": len(results),
        "results": results,
    }
