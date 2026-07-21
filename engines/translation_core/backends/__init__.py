"""Built-in TranslationBackend plugins."""

from __future__ import annotations

from engines.translation_core.backends.heuristic import HeuristicBackend
from engines.translation_core.backends.identity import IdentityBackend
from engines.translation_core.backends.mt_bridge import MTBridgeBackend

__all__ = ["IdentityBackend", "HeuristicBackend", "MTBridgeBackend"]
