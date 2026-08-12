"""Spec v3 exception hierarchy.

Explicit, catchable errors so the pipeline never silently degrades to a
wrong-language or semantically-mangled dub. All errors inherit from
``SpecV3Error`` so callers can catch the family without listing subclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SpecV3Error(Exception):
    """Base class for spec v3 pipeline failures."""

    code: str = "spec_v3_error"

    def __init__(self, message: str, *, code: str | None = None, **context: Any) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.context: dict[str, Any] = dict(context or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "context": self.context,
        }


class LanguageLeakError(SpecV3Error):
    """Translated / synthesized text is not in the requested target language."""

    code = "language_leak"


class SemanticIntegrityError(SpecV3Error):
    """Translated text lost >X% of source meaning (entities, numbers, negation)."""

    code = "semantic_integrity_lost"


class TimingBudgetError(SpecV3Error):
    """A segment cannot fit its allocated slot even after duration control."""

    code = "timing_budget_overflow"


class VoiceIdentityError(SpecV3Error):
    """Cloned voice failed cosine verification after all retries."""

    code = "voice_identity_mismatch"


@dataclass
class SpecV3ErrorRecord:
    """Structured record for OpenDDF lineage — never raise, just record."""

    code: str
    stage: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "context": self.context,
        }
