"""Live translation configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class LiveConfig:
    latency_target_ms: int
    chunk_seconds: float
    stt_model: str
    use_enterprise: bool
    use_naturalizer: bool
    subtitles: bool
    simulate_only: bool

    @classmethod
    def from_env(cls) -> "LiveConfig":
        return cls(
            latency_target_ms=_int("VM_LIVE_LATENCY_TARGET_MS", 5000),
            chunk_seconds=_float("VM_LIVE_CHUNK_SECONDS", 4.0),
            stt_model=(os.getenv("VM_LIVE_STT_MODEL") or "tiny").strip(),
            use_enterprise=(os.getenv("VM_LIVE_ENTERPRISE") or "0").strip().lower()
            in ("1", "true", "yes"),
            use_naturalizer=(os.getenv("VM_LIVE_NATURALIZER") or "1").strip().lower()
            not in ("0", "false", "no"),
            subtitles=(os.getenv("VM_LIVE_SUBTITLES") or "1").strip().lower()
            not in ("0", "false", "no"),
            simulate_only=(os.getenv("VM_LIVE_SIMULATE_ONLY") or "0").strip().lower()
            in ("1", "true", "yes"),
        )


def live_config() -> LiveConfig:
    return LiveConfig.from_env()
