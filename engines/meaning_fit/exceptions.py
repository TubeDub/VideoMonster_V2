"""MF1 — Meaning Fit errors."""

from __future__ import annotations


class MeaningFitError(Exception):
    """Base Meaning Fit error."""

    code = "meaning_fit_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class TruncateNotMeaningFitError(MeaningFitError):
    """truncate_to_n_chars (or chop) must never count as Meaning Fit success."""

    code = "truncate_not_meaning_fit"
