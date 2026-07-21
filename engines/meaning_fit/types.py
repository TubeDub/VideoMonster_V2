"""MF1 — Meaning Fit core types (skeleton)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

FitStatus = Literal[
    "noop",
    "already_fits",
    "paraphrase_shorten",
    "paraphrase_expand",
    "fit_failed",
    "rejected_truncate",
]

FitVerdict = Literal["OK", "TOO_LONG", "TOO_SHORT", "UNKNOWN"]


@dataclass
class MeaningText:
    """UK text candidate for duration-aware meaning fit."""

    text: str
    lang: str = "uk"
    source: str = "translation"
    meta: dict[str, Any] = field(default_factory=dict)

    def stripped(self) -> str:
        return str(self.text or "").strip()


@dataclass
class FitRequest:
    """Request to fit UK text into an EN slot duration."""

    text_uk: str
    slot_ms: int
    original_en: str = ""
    segment_id: str = ""
    allow_shorten: bool = True
    allow_expand: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FitResult:
    """Outcome of Meaning Fit (skeleton: mostly no-op / reject truncate)."""

    text_uk: str
    status: FitStatus = "noop"
    reason: str = ""
    predicted_ms: int | None = None
    slot_ms: int | None = None
    verdict: FitVerdict = "UNKNOWN"
    success: bool = False
    needs_manual: bool = False
    method: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text_uk": self.text_uk,
            "status": self.status,
            "reason": self.reason,
            "predicted_ms": self.predicted_ms,
            "slot_ms": self.slot_ms,
            "verdict": self.verdict,
            "success": self.success,
            "needs_manual": self.needs_manual,
            "method": self.method,
            "meta": dict(self.meta),
        }
