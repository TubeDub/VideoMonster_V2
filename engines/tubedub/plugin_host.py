"""Universal Plugin Host — all plugins register and execute here."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class PluginKind(str, Enum):
    FX = "fx"
    PIPELINE_STAGE = "pipeline_stage"
    TRANSLATION = "translation"
    TTS = "tts"
    STORAGE = "storage"
    UTILITY = "utility"


@dataclass
class PluginRecord:
    plugin_id: str
    label: str
    kind: str = PluginKind.UTILITY.value
    backend: str = "builtin"
    module_id: str = ""
    enabled: bool = True
    schema: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "label": self.label,
            "kind": self.kind,
            "backend": self.backend,
            "module_id": self.module_id,
            "enabled": self.enabled,
            "schema": dict(self.schema),
            "meta": dict(self.meta),
        }


Processor = Callable[..., Any]


class PluginHost:
    """Single plugin registry for FX, pipeline stages, and future VST/VST3."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._plugins: dict[str, PluginRecord] = {}
        self._processors: dict[str, Processor] = {}
        self._vst_bridge: Processor | None = None

    def register(
        self,
        record: PluginRecord,
        processor: Processor | None = None,
    ) -> None:
        with self._lock:
            self._plugins[record.plugin_id] = record
            if processor:
                self._processors[record.plugin_id] = processor

    def unregister(self, plugin_id: str) -> None:
        with self._lock:
            self._plugins.pop(plugin_id, None)
            self._processors.pop(plugin_id, None)

    def set_vst_bridge(self, fn: Processor) -> None:
        with self._lock:
            self._vst_bridge = fn

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._plugins.get(plugin_id)

    def list_plugins(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._plugins.values())
        if kind:
            rows = [r for r in rows if r.kind == kind]
        return [r.to_dict() for r in sorted(rows, key=lambda r: r.plugin_id)]

    def invoke(
        self,
        plugin_id: str,
        *,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        rec = self.get(plugin_id)
        if not rec or not rec.enabled:
            raise KeyError(f"Plugin not available: {plugin_id}")
        proc = self._processors.get(plugin_id)
        if proc:
            return proc(dict(payload or {}), **kwargs)
        backend = rec.backend.lower()
        if backend in ("vst", "vst3"):
            if self._vst_bridge is None:
                raise RuntimeError(f"VST bridge not configured for {plugin_id}")
            return self._vst_bridge(plugin_id=plugin_id, backend=backend, payload=payload, **kwargs)
        raise RuntimeError(f"No processor for plugin {plugin_id}")

    def import_dub_studio_fx(self) -> int:
        """Register dub_studio builtin FX into universal host (architecture boundary)."""
        try:
            from engines.dub_studio.fx.registry import FX_REGISTRY, get_plugin

            count = 0
            for pid in FX_REGISTRY:
                inst = get_plugin(pid)
                meta = inst.to_dict()

                def _make(p_id: str):
                    def _proc(payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
                        from engines.dub_studio.fx.base import EffectContext

                        inp = Path(str(payload.get("input_path") or kw.get("input_path")))
                        out = Path(str(payload.get("output_path") or kw.get("output_path")))
                        p = get_plugin(p_id)
                        ctx = EffectContext(**dict(payload.get("ctx") or {}))
                        res = p.process(inp, out, params=dict(payload.get("params") or {}), ctx=ctx)
                        return {"output_path": res.output_path, "meta": dict(res.meta)}

                    return _proc

                self.register(
                    PluginRecord(
                        plugin_id=pid,
                        label=str(meta.get("label") or pid),
                        kind=PluginKind.FX.value,
                        backend="builtin",
                        module_id="dub_studio",
                        schema=dict(meta.get("schema") or {}),
                    ),
                    processor=_make(pid),
                )
                count += 1
            return count
        except Exception:
            return 0


_HOST: PluginHost | None = None
_HOST_LOCK = threading.Lock()


def get_plugin_host() -> PluginHost:
    global _HOST
    with _HOST_LOCK:
        if _HOST is None:
            _HOST = PluginHost()
        return _HOST
