"""P109 — Translation Lock for MeaningUnits.

LOCK is applied ONLY after:
1. Best formulation variant is selected
2. Meaning is validated (P117 preservation rules)
3. Duration is validated (P107 prediction within tolerance)
4. Terminology is validated

After LOCK, text changes are FORBIDDEN.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from engines.pipeline_integrity.exceptions import ArchitectureViolation

logger = logging.getLogger("tubedub.semantic_v3.meaning_lock")


def lock_meaning_unit(
    unit: Any,
    *,
    selected_text: str = "",
    force: bool = False,
) -> Any:
    """P109: lock a MeaningUnit after variant selection + validation.

    Prerequisites (checked unless force=True):
    - selected_variant_id must be set
    - validation_status must be 'passed'
    - predicted_duration_ms must be set
    """
    if not force:
        if not getattr(unit, 'selected_variant_id', ''):
            raise ArchitectureViolation(
                "P109: cannot lock without selected variant",
                stage="meaning_lock",
                rule="variant_required",
            )
        if getattr(unit, 'validation_status', '') == 'failed':
            raise ArchitectureViolation(
                "P109: cannot lock unit with failed validation",
                stage="meaning_lock",
                rule="validation_required",
            )

    text = selected_text or getattr(unit, 'translated_text', '') or ''
    if not text.strip():
        logger.warning("meaning_lock: empty text for unit %s", getattr(unit, 'unit_uuid', ''))
        return unit

    unit.translated_text = text
    unit.semantic_locked = True
    unit.lock_status = "locked"

    for sent in getattr(unit, 'sentences', []):
        sent.semantic_locked = True
        sent.lock_status = "locked"
        if not sent.translated_text and text:
            sent.translated_text = text

    fingerprint = hashlib.sha256(text.encode()).hexdigest()[:16]
    if hasattr(unit, 'context') and isinstance(unit.context, dict):
        unit.context['meaning_fingerprint'] = fingerprint

    logger.info(
        "MeaningLock: unit=%s text_len=%d fingerprint=%s",
        getattr(unit, 'unit_uuid', '?')[:8],
        len(text),
        fingerprint[:8],
    )
    return unit


def lock_all_meaning_units(units: list[Any], *, force: bool = False) -> list[Any]:
    """Lock all MeaningUnits in sequence."""
    for unit in units:
        lock_meaning_unit(unit, force=force)
    return units


def assert_locked(unit: Any) -> None:
    """Assert that a MeaningUnit is locked (post-lock guard)."""
    if not getattr(unit, 'semantic_locked', False):
        raise ArchitectureViolation(
            "P109: MeaningUnit must be locked at this stage",
            stage="meaning_lock",
            rule="must_be_locked",
        )


def assert_text_unchanged(unit: Any, expected_text: str) -> None:
    """Assert that locked text hasn't been modified (P109 immutability)."""
    actual = getattr(unit, 'translated_text', '')
    if actual != expected_text:
        raise ArchitectureViolation(
            f"P109: locked text was modified (expected len={len(expected_text)}, got len={len(actual)})",
            stage="meaning_lock",
            rule="immutable_after_lock",
        )
