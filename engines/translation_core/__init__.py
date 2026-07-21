"""VideoMonster Translation Core — Master Spec Part 3.

Meaning → Translation → Validation → Semantic Lock

Independent of Scheduler, Dub Engine, TTS, Merge, Studio.
"""

from __future__ import annotations

from engines.translation_core.engine import (
    assert_sentence_only,
    translate_sentences,
    translation_core_info,
)
from engines.translation_core.registry import get_backend, list_backends, register_backend
from engines.translation_core.terminology import TerminologyManager
from engines.translation_core.types import (
    SentenceTranslationReport,
    TranslationCoreResult,
    TranslationVariant,
)

__all__ = [
    "TerminologyManager",
    "SentenceTranslationReport",
    "TranslationCoreResult",
    "TranslationVariant",
    "assert_sentence_only",
    "get_backend",
    "list_backends",
    "register_backend",
    "translate_sentences",
    "translation_core_info",
]
