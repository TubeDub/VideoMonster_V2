"""Plugin Architecture scaffold — Dub Engine Stabilization TZ v2.0 P10.

Modules register as plugins so Whisper / Translation / TTS / Scheduler
can be swapped without rewriting the pipeline core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class PipelinePlugin(Protocol):
    id: str
    kind: str  # whisper | translation | tts | scheduler | merge | alignment

    def is_available(self) -> bool: ...


@dataclass
class PluginInfo:
    id: str
    kind: str
    available: bool
    meta: dict[str, Any] = field(default_factory=dict)


_REGISTRY: dict[str, dict[str, Any]] = {}


def register_plugin(
    kind: str,
    plugin_id: str,
    *,
    factory: Callable[[], Any],
    meta: dict[str, Any] | None = None,
) -> None:
    _REGISTRY.setdefault(kind, {})[plugin_id] = {
        "factory": factory,
        "meta": dict(meta or {}),
    }


def list_plugins(kind: str | None = None) -> list[PluginInfo]:
    out: list[PluginInfo] = []
    kinds = [kind] if kind else list(_REGISTRY.keys())
    for k in kinds:
        for pid, rec in (_REGISTRY.get(k) or {}).items():
            inst = None
            available = False
            try:
                inst = rec["factory"]()
                available = bool(getattr(inst, "is_available", lambda: True)())
            except Exception:
                available = False
            out.append(
                PluginInfo(
                    id=pid,
                    kind=k,
                    available=available,
                    meta=dict(rec.get("meta") or {}),
                )
            )
    return out


def get_plugin(kind: str, plugin_id: str | None = None) -> Any:
    bucket = _REGISTRY.get(kind) or {}
    if not bucket:
        raise KeyError(f"no plugins registered for kind={kind}")
    if plugin_id and plugin_id in bucket:
        return bucket[plugin_id]["factory"]()
    # First available
    for pid, rec in bucket.items():
        inst = rec["factory"]()
        if getattr(inst, "is_available", lambda: True)():
            return inst
    # Fallback first
    return next(iter(bucket.values()))["factory"]()


def bootstrap_builtin_plugins() -> None:
    """Register built-in adapters (idempotent)."""
    if _REGISTRY:
        return

    def _scheduler_factory():
        from engines.scheduler import Scheduler

        return Scheduler()

    register_plugin("scheduler", "default", factory=_scheduler_factory, meta={"contract": 1})

    def _mock_tts():
        from engines.tts_engines.providers import MockTTSEngine

        return MockTTSEngine()

    def _edge_tts():
        from engines.tts_engines.edge_engine import EdgeTTSEngine

        return EdgeTTSEngine()

    register_plugin("tts", "mock", factory=_mock_tts, meta={"contract": 1})
    register_plugin("tts", "edge-offline", factory=_edge_tts, meta={"contract": 1})

    try:
        from engines.tts_engines.providers import provider_engines

        for eng in provider_engines():
            register_plugin(
                "tts",
                eng.id,
                factory=lambda e=eng: e,
                meta={"contract": 1, "provider": eng.name},
            )
    except Exception:
        pass
