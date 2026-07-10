"""DSP plugin pipeline — Chain of Responsibility for audio effects."""

from engines.dub_studio.fx.base import EffectContext, EffectModule, ProcessResult
from engines.dub_studio.fx.chain import FxChain, FxPipeline
from engines.dub_studio.fx.registry import FX_REGISTRY, get_plugin, list_plugins

__all__ = [
    "EffectContext",
    "EffectModule",
    "FxChain",
    "FxPipeline",
    "FX_REGISTRY",
    "ProcessResult",
    "get_plugin",
    "list_plugins",
]
