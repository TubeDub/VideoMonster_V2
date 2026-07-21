"""P17.4 — Configuration Freeze snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engines.audio_timing_optimizer import (
    BORROW_MAX_MS,
    CROSSFADE_MS_DEFAULT,
    MICRO_STRETCH_MAX,
    MICRO_STRETCH_MIN,
    TEMPO_MAX,
    TEMPO_MIN,
)
from engines.pipeline_integrity.contract_versions import CONTRACT_VERSIONS
from engines.release_governance.versions import collect_version_bundle


def collect_frozen_config() -> dict[str, Any]:
    """Capture release-critical knobs that must not drift without a PR."""
    versions = collect_version_bundle()
    return {
        "versions": versions,
        "contracts": dict(CONTRACT_VERSIONS),
        "scheduler": {
            "sole_timing_mutator": True,
            "api": ["update_time", "request_time"],
        },
        "audio_timing_optimizer": {
            "tempo_min": TEMPO_MIN,
            "tempo_max": TEMPO_MAX,
            "micro_stretch_min": MICRO_STRETCH_MIN,
            "micro_stretch_max": MICRO_STRETCH_MAX,
            "crossfade_ms_default": CROSSFADE_MS_DEFAULT,
            "borrow_max_ms": BORROW_MAX_MS,
        },
        "tts": {
            "providers_registry": "engines.tts_engines.providers",
            "note": "Installed backend packages are environment-specific; adapters are frozen.",
        },
        "models": {
            "note": "Model weights/paths are environment-specific; contract versions are frozen.",
        },
    }


def write_config_freeze(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = collect_frozen_config()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_config_freeze(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def assert_config_matches_freeze(
    frozen: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
) -> list[str]:
    """Return list of drift violations (empty = OK)."""
    current = current or collect_frozen_config()
    issues: list[str] = []
    for key in ("contracts", "audio_timing_optimizer", "scheduler"):
        if frozen.get(key) != current.get(key):
            issues.append(f"config_drift:{key}")
    fv = (frozen.get("versions") or {}).get("contract_versions")
    cv = (current.get("versions") or {}).get("contract_versions")
    if fv and cv and fv != cv:
        issues.append("config_drift:contract_versions")
    return issues
