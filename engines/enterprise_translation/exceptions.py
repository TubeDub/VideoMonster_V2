"""Enterprise translation exceptions."""

from __future__ import annotations


class IntegrityException(Exception):
    """Placeholder contract violated — pipeline must not continue with damaged data."""

    def __init__(self, message: str, *, stage: str = "", details: dict | None = None):
        super().__init__(message)
        self.stage = stage
        self.details = details or {}
