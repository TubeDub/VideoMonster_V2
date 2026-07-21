"""Single Owner registry — one writer per text operation (TPS2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


class DualWriterError(RuntimeError):
    """Raised when two writers claim the same text operation."""


@dataclass
class OwnerRegistry:
    """Process-level registry of writers that mutated a segment this run."""

    _locks: dict[str, str] = field(default_factory=dict)
    _history: list[dict[str, Any]] = field(default_factory=list)
    _mu: Lock = field(default_factory=Lock)
    dual_writer_violations: int = 0

    # Canonical owners (TZ Part 6)
    OWNERS = {
        "mt_raw": "MTEngine",
        "naturalize": "Naturalizer",
        "semantic_rewrite": "SemanticRewriteOwner",
        "grammar_rewrite": "GrammarRewriteOwner",
        "timing_text_adapt": "TimingMeaningFitOwner",
        "final_approve": "TQE",
        "approved_text": "ApprovedTextAPI",
    }

    def claim(self, operation: str, writer: str, *, segment_index: int = -1) -> None:
        expected = self.OWNERS.get(operation)
        with self._mu:
            key = f"{operation}:{segment_index}"
            prev = self._locks.get(key)
            entry = {
                "operation": operation,
                "writer": writer,
                "segment_index": segment_index,
                "expected_owner": expected,
            }
            if expected and writer != expected and writer not in (
                expected,
                f"{expected}",
            ):
                # Allow aliases that map to the same owner family
                aliases = {
                    "TranslationAgent": "MTEngine",
                    "ArgosEngine": "MTEngine",
                    "MarianEngine": "MTEngine",
                    "NaturalizerV1": "Naturalizer",
                    "NaturalizerV2": "Naturalizer",
                    "SemanticAgent": "SemanticRewriteOwner",
                    "GrammarAgent": "GrammarRewriteOwner",
                    "TimingAgent": "TimingMeaningFitOwner",
                    "MeaningFitEngine": "TimingMeaningFitOwner",
                    "DSAL": "TimingMeaningFitOwner",
                }
                canonical = aliases.get(writer, writer)
                if canonical != expected:
                    self.dual_writer_violations += 1
                    entry["violation"] = "unexpected_writer"
                    self._history.append(entry)
                    raise DualWriterError(
                        f"operation={operation} expected_owner={expected} got={writer}"
                    )
            if prev and prev != writer and prev != expected:
                # Second distinct writer on same op+segment
                aliases_rev = {
                    "TranslationAgent": "MTEngine",
                    "ArgosEngine": "MTEngine",
                    "SemanticAgent": "SemanticRewriteOwner",
                    "GrammarAgent": "GrammarRewriteOwner",
                    "TimingAgent": "TimingMeaningFitOwner",
                    "MeaningFitEngine": "TimingMeaningFitOwner",
                    "DSAL": "TimingMeaningFitOwner",
                    "NaturalizerV1": "Naturalizer",
                    "NaturalizerV2": "Naturalizer",
                }
                a = aliases_rev.get(prev, prev)
                b = aliases_rev.get(writer, writer)
                if a != b:
                    self.dual_writer_violations += 1
                    entry["violation"] = "dual_writer"
                    entry["previous_writer"] = prev
                    self._history.append(entry)
                    raise DualWriterError(
                        f"dual writer on {operation} seg={segment_index}: {prev} then {writer}"
                    )
            self._locks[key] = writer
            self._history.append(entry)

    def timing_adapt_count(self, segment_index: int) -> int:
        with self._mu:
            return sum(
                1
                for h in self._history
                if h.get("operation") == "timing_text_adapt"
                and h.get("segment_index") == segment_index
                and not h.get("violation")
            )

    def to_dict(self) -> dict[str, Any]:
        with self._mu:
            return {
                "dual_writer_violations": self.dual_writer_violations,
                "claims": len(self._locks),
                "history_tail": list(self._history[-50:]),
            }


# Task-scoped registries
_REGISTRIES: dict[str, OwnerRegistry] = {}
_REG_MU = Lock()


def get_owner_registry(task_id: str) -> OwnerRegistry:
    tid = str(task_id or "_")
    with _REG_MU:
        if tid not in _REGISTRIES:
            _REGISTRIES[tid] = OwnerRegistry()
        return _REGISTRIES[tid]


def clear_owner_registry(task_id: str) -> None:
    with _REG_MU:
        _REGISTRIES.pop(str(task_id or "_"), None)
