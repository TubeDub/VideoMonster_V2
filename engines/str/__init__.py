"""Smart Translation Router (STR) — plugin-based MT selection with self-learning."""

from engines.str.config import use_str
from engines.str.knowledge_base import (
    engine_stats,
    load_knowledge_base,
    pair_summary,
    record_translation,
)
from engines.str.ranking import engine_order_ids, ranked_engines_for_pair
from engines.str.router import ensure_str_ready, str_engine_rankings, translate_with_str
from engines.str.types import STRTranslationResult

__all__ = [
    "STRTranslationResult",
    "engine_order_ids",
    "engine_stats",
    "ensure_str_ready",
    "load_knowledge_base",
    "pair_summary",
    "ranked_engines_for_pair",
    "record_translation",
    "str_engine_rankings",
    "translate_with_str",
    "use_str",
]
