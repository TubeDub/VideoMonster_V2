"""Enterprise Translation Pipeline configuration."""

from __future__ import annotations

import os

ENTERPRISE_VERSION = 1
REGISTRY_FILE = "enterprise_placeholder_registry.json"
HEALTH_FILE = "enterprise_engine_health.json"
SERIALIZER_BENCH_FILE = "enterprise_serializer_benchmark.json"
DEV_LOG_NAME = "enterprise_translation.log"

DEFAULT_ENGINE_TIMEOUT_SEC = 45.0
DEFAULT_TOURNAMENT_MAX_ENGINES = 4
DEFAULT_BENCHMARK_SAMPLES = 20  # lightweight; full bench uses 100 when VM_ET_BENCH_FULL=1


def use_enterprise_translation() -> bool:
    v = (os.getenv("VM_ENTERPRISE_TRANSLATION") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def architect_mode() -> bool:
    return os.getenv("VM_ARCHITECT_MODE", "").strip().lower() in ("1", "true", "yes", "on") or (
        os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    )


def tournament_max_engines() -> int:
    raw = (os.getenv("VM_ET_MAX_ENGINES") or "").strip()
    if raw.isdigit():
        return max(1, min(8, int(raw)))
    return DEFAULT_TOURNAMENT_MAX_ENGINES


def engine_timeout_sec() -> float:
    raw = (os.getenv("VM_ET_ENGINE_TIMEOUT") or "").strip()
    if raw:
        try:
            return max(5.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_ENGINE_TIMEOUT_SEC


def strict_contract() -> bool:
    """Raise IntegrityException on contract violation (default on)."""
    v = (os.getenv("VM_ET_STRICT_CONTRACT") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")
