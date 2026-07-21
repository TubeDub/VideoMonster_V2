"""P202 — Backend Registry (plugin translators, no core edits for new models)."""

from __future__ import annotations

import logging
import os
from typing import Callable

from engines.translation_core.backend import TranslationBackend

logger = logging.getLogger("tubedub.translation_core.registry")

_REGISTRY: dict[str, Callable[[], TranslationBackend]] = {}
_INSTANCES: dict[str, TranslationBackend] = {}


def register_backend(backend_id: str, factory: Callable[[], TranslationBackend]) -> None:
    _REGISTRY[backend_id] = factory


def list_backends() -> list[str]:
    _ensure_builtins()
    return sorted(_REGISTRY.keys())


def get_backend(backend_id: str | None = None) -> TranslationBackend:
    _ensure_builtins()
    bid = (backend_id or os.environ.get("VM_TRANSLATION_BACKEND", "") or "mt_bridge").strip()
    if bid not in _REGISTRY:
        bid = "identity" if "identity" in _REGISTRY else next(iter(_REGISTRY))
    if bid not in _INSTANCES:
        eng = _REGISTRY[bid]()
        eng.initialize()
        _INSTANCES[bid] = eng
    return _INSTANCES[bid]


def shutdown_all() -> None:
    for eng in _INSTANCES.values():
        try:
            eng.shutdown()
        except Exception as exc:
            logger.warning("backend shutdown failed: %s", exc)
    _INSTANCES.clear()


def _ensure_builtins() -> None:
    if _REGISTRY:
        return
    from engines.translation_core.backends.heuristic import HeuristicBackend
    from engines.translation_core.backends.identity import IdentityBackend
    from engines.translation_core.backends.mt_bridge import MTBridgeBackend

    register_backend("identity", IdentityBackend)
    register_backend("heuristic", HeuristicBackend)
    register_backend("mt_bridge", MTBridgeBackend)
    # Aliases for future / documented plugins
    register_backend("nllb", MTBridgeBackend)
    register_backend("marian", MTBridgeBackend)
    register_backend("gpt", MTBridgeBackend)
    register_backend("qwen", MTBridgeBackend)
    register_backend("deepseek", MTBridgeBackend)
