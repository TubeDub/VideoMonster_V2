# -*- coding: utf-8 -*-
"""Unified Language Validation service (single entry point for TubeDub gates)."""

from engines.language_validation.service import (
    LanguageValidationDecision,
    format_validation_message,
    validate_language,
    validate_segments,
)
from engines.language_validation.diagnostics import (
    write_language_validation_diagnostics,
)
from engines.language_validation.recovery import (
    apply_recovery_and_revalidate,
    recover_language_issues,
)

__all__ = [
    "LanguageValidationDecision",
    "format_validation_message",
    "validate_language",
    "validate_segments",
    "write_language_validation_diagnostics",
    "recover_language_issues",
    "apply_recovery_and_revalidate",
]
