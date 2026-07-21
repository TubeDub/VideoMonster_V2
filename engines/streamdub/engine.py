"""StreamDub Engine V1 — public entry point."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from engines.streamdub.pipeline.orchestrator import StreamDubOrchestrator
from engines.streamdub.types import StreamDubMode, StreamDubRequest, StreamDubResult

logger = logging.getLogger("tubedub.streamdub")

_ENGINE: StreamDubOrchestrator | None = None


def get_engine(app_dir: Path | None = None) -> StreamDubOrchestrator:
    global _ENGINE
    root = Path(app_dir) if app_dir else Path(__file__).resolve().parents[2]
    if _ENGINE is None:
        _ENGINE = StreamDubOrchestrator(root)
        _ENGINE.initialize()
    return _ENGINE


async def run_streamdub(request: StreamDubRequest, *, app_dir: Path | None = None) -> StreamDubResult:
    engine = get_engine(app_dir)
    return await engine.run(request)


def run_streamdub_sync(request: StreamDubRequest, *, app_dir: Path | None = None) -> StreamDubResult:
    import asyncio

    return asyncio.run(run_streamdub(request, app_dir=app_dir))


def parse_mode(value: str | None) -> StreamDubMode:
    key = (value or "smart").strip().lower()
    for m in StreamDubMode:
        if m.value == key:
            return m
    return StreamDubMode.SMART


def engine_info(app_dir: Path | None = None) -> dict[str, Any]:
    eng = get_engine(app_dir)
    return {
        "engine": "StreamDub",
        "version": "1.0",
        "modes": [m.value for m in StreamDubMode],
        "health": eng.health_check(),
        "capabilities": eng.capabilities(),
    }
