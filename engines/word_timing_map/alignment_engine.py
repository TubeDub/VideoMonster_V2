"""Pluggable Alignment Engine — Word Timing Map → Meaning Units (Phase 2)."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

from engines.word_timing_map.models import AlignedSegmentMap, SegmentWordMap


class AlignmentEngine(ABC):
    """
    Universal interface — replace implementation without changing pipeline.

    Word Timing Map → Alignment Engine → Meaning Units → Optimizer
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def align(
        self,
        source_map: SegmentWordMap,
        target_text: str,
        *,
        source_hint: str = "",
        tgt_lang: str = "uk",
        src_lang: str = "en",
    ) -> AlignedSegmentMap:
        ...


class HeuristicAlignmentEngine(AlignmentEngine):
    """
    MVP: word order, gaps, anchors (names/numbers/dates/currency), punctuation, block length.
    Phase 2 implementation — not wired in Phase 1.
    """

    @property
    def name(self) -> str:
        return "heuristic"

    def align(
        self,
        source_map: SegmentWordMap,
        target_text: str,
        *,
        source_hint: str = "",
        tgt_lang: str = "uk",
        src_lang: str = "en",
    ) -> AlignedSegmentMap:
        raise NotImplementedError(
            "HeuristicAlignmentEngine is Phase 2. "
            "Phase 1 only persists source_word_map through the pipeline."
        )


class PassthroughAlignmentEngine(AlignmentEngine):
    """Phase 1 stub — no alignment, empty units list."""

    @property
    def name(self) -> str:
        return "passthrough"

    def align(
        self,
        source_map: SegmentWordMap,
        target_text: str,
        *,
        source_hint: str = "",
        tgt_lang: str = "uk",
        src_lang: str = "en",
    ) -> AlignedSegmentMap:
        return AlignedSegmentMap(
            segment_index=source_map.segment_index,
            source_words=list(source_map.words),
            target_text=str(target_text or "").strip(),
            units=[],
            timing_source=source_map.timing_source,
            optimization_required=False,
        )


def get_alignment_engine(name: str | None = None) -> AlignmentEngine:
    engine = (name or os.getenv("VM_WTM_ALIGN_ENGINE", "heuristic")).strip().lower()
    if engine in ("passthrough", "none", "off"):
        return PassthroughAlignmentEngine()
    if engine == "heuristic":
        return HeuristicAlignmentEngine()
    raise ValueError(f"Unknown alignment engine: {engine}")
