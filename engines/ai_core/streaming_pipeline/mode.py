"""AI Core 4.2 — pipeline mode selection (not a separate agent)."""

from __future__ import annotations

import os
from typing import Any

PIPELINE_MODE_BATCH = "batch"
PIPELINE_MODE_STREAMING = "streaming"
AI_CORE_VERSION_STREAMING = "4.3"

# Full conveyor order (mode, not agents).
STREAM_STAGES = (
    "translation",
    "semantic",
    "timing",
    "grammar",
    "quality",
    "reviewer",
    "voice_preparation",
    "voice",
)

# Backward-compatible alias
TEXT_STREAM_STAGES = STREAM_STAGES


def resolve_pipeline_mode(state: dict[str, Any] | None = None) -> str:
    """batch (default in tests) | streaming (production default when env set)."""
    if state and str(state.get("pipeline_mode") or "").strip():
        return str(state["pipeline_mode"]).strip().lower()
    env = str(os.environ.get("AI_CORE_PIPELINE_MODE") or "batch").strip().lower()
    return env if env in (PIPELINE_MODE_BATCH, PIPELINE_MODE_STREAMING) else PIPELINE_MODE_STREAMING


def streaming_stages_in_chain(chain_names: list[str]) -> tuple[str, ...]:
    """Which stages to run inside the streaming block for this chain."""
    present = [s for s in STREAM_STAGES if s in chain_names]
    return tuple(present)
