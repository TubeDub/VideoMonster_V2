"""VideoMonster V3 — Semantic Translation + Intelligent Dub (Meaning First).

Whisper is ASR + word timestamps only. Pipeline units are SemanticSentence /
SpeechUnit / AudioUnit. Enabled via VM_SEMANTIC_V3=1 or feature flag semantic_v3.

Phase 2 (P31–P50): native Meaning Pipeline — no Whisper bridge.
"""

from __future__ import annotations

import os

from engines.semantic_v3.pipeline import run_semantic_v3_from_asr
from engines.semantic_v3.types import (
    MeaningUnit,
    SemanticProject,
    SemanticSentence,
    SemanticWord,
)


def semantic_v3_enabled() -> bool:
    env = str(os.environ.get("VM_SEMANTIC_V3", "")).strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from engines.core.feature_flags import is_enabled

        return bool(is_enabled("semantic_v3", developer_session=True))
    except Exception:
        return False


def semantic_v3_native_te_enabled() -> bool:
    """P31 — native Sentence→TE path.

    Default OFF: native TE inside Phase2 blocked the UI at 15% for tens of
    minutes (Marian/LLM) before the legacy translate stage. Opt in via
    VM_SEMANTIC_V3_NATIVE_TE=1 when explicitly needed.
    """
    env = str(os.environ.get("VM_SEMANTIC_V3_NATIVE_TE", "0")).strip().lower()
    return env in ("1", "true", "yes", "on")


def run_semantic_v3_phase2(*args, **kwargs):
    from engines.semantic_v3.phase2 import run_semantic_v3_phase2 as _run

    return _run(*args, **kwargs)


def phase2_to_orchestrator_arrays(*args, **kwargs):
    from engines.semantic_v3.phase2 import phase2_to_orchestrator_arrays as _export

    return _export(*args, **kwargs)


def run_semantic_core_pipeline(*args, **kwargs):
    from engines.semantic_v3.semantic_core import run_semantic_core

    return run_semantic_core(*args, **kwargs)


def build_meaning_units(*args, **kwargs):
    from engines.semantic_v3.meaning_unit_builder import build_meaning_units as _build

    return _build(*args, **kwargs)


def run_meaning_first_pipeline(*args, **kwargs):
    from engines.semantic_v3.meaning_first_pipeline import (
        run_meaning_first_pipeline as _run,
    )

    return _run(*args, **kwargs)


__all__ = [
    "MeaningUnit",
    "SemanticSentence",
    "SemanticWord",
    "SemanticProject",
    "build_meaning_units",
    "run_meaning_first_pipeline",
    "run_semantic_v3_from_asr",
    "run_semantic_v3_phase2",
    "phase2_to_orchestrator_arrays",
    "run_semantic_core_pipeline",
    "semantic_v3_enabled",
    "semantic_v3_native_te_enabled",
]
