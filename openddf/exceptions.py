"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0
Copyright (c) 2026 OpenDDF Contributors. All rights reserved.

Licensed for use under the OpenDDF Specification v0.1.0.
"""

from __future__ import annotations

from typing import Any


class DDFError(Exception):
    """Base class for all OpenDDF framework exceptions."""


class StageSnapshotIntegrityError(DDFError):
    """Raised when a disallowed field mutation is detected."""

    def __init__(
        self,
        message: str = "",
        *,
        field_name: str = "",
        old_value: Any = None,
        new_value: Any = None,
        allowed_mutations: list[str] | None = None,
        location_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or f"Disallowed mutation of field {field_name!r}")
        self.field_name = field_name
        self.old_value = old_value
        self.new_value = new_value
        self.allowed_mutations = list(allowed_mutations or [])
        self.location_info = dict(location_info or {})
