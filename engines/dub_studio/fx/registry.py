"""FX plugin registry."""

from __future__ import annotations

from engines.dub_studio.fx.base import EffectModule
from engines.dub_studio.fx.builtins import (
    CompressorPlugin,
    DeEsserPlugin,
    EqPlugin,
    HighPassPlugin,
    LimiterPlugin,
    NormalizePlugin,
    _Passthrough,
)

FX_REGISTRY: dict[str, type[EffectModule]] = {
    "passthrough": _Passthrough,
    "highpass": HighPassPlugin,
    "eq": EqPlugin,
    "compressor": CompressorPlugin,
    "limiter": LimiterPlugin,
    "deesser": DeEsserPlugin,
    "normalize": NormalizePlugin,
}


def get_plugin(plugin_id: str) -> EffectModule:
    cls = FX_REGISTRY.get(plugin_id)
    if not cls:
        raise KeyError(f"Unknown FX plugin: {plugin_id}")
    return cls()


def list_plugins() -> list[dict]:
    return [get_plugin(pid).to_dict() for pid in FX_REGISTRY]
