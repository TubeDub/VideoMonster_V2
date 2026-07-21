"""Engine / product version stamps for release governance."""

from __future__ import annotations

from typing import Any

from engines.perf_budgets import BUDGETS
from engines.pipeline_integrity.contract_versions import CONTRACT_VERSIONS

# Product-facing engine versions (bump on intentional breaking/behavior change).
TRANSLATION_ENGINE_VERSION = "1.0.0"
DUB_ENGINE_VERSION = "1.0.0"
SYSTEM_VERSION = "2.0.0-rc1"


def collect_version_bundle() -> dict[str, Any]:
    return {
        "system_version": SYSTEM_VERSION,
        "translation_engine_version": TRANSLATION_ENGINE_VERSION,
        "dub_engine_version": DUB_ENGINE_VERSION,
        "contract_versions": dict(CONTRACT_VERSIONS),
        "performance_budgets_ms": dict(BUDGETS),
    }
