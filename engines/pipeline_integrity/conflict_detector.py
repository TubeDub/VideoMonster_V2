"""Conflict Detector — Final v3.0 P0.

Before mutating a field:
  Who owns it? Is write allowed? Contract version OK? Data current?
Violation → operation blocked (no silent mutation).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engines.pipeline_integrity.contract_versions import (
    CONTRACT_VERSIONS,
    require_contract_versions,
)
from engines.pipeline_integrity.exceptions import (
    ContractVersionError,
    PipelineIntegrityError,
    TranslationLockError,
)
from engines.pipeline_integrity.translation_lock import (
    FIELD_OWNERS,
    LOCKED_TEXT_FIELDS,
    assert_owner_may_write,
    is_segment_locked,
)


class ConflictDetectorError(PipelineIntegrityError):
    code = "conflict_detector"


@dataclass
class ConflictCheckResult:
    ok: bool
    field: str
    owner: str
    requested_by: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "field": self.field,
            "owner": self.owner,
            "requested_by": self.requested_by,
            "detail": self.detail,
        }


def check_field_write(
    field: str,
    *,
    requested_by: str,
    segment: dict[str, Any] | None = None,
    info: dict[str, Any] | None = None,
    enforce: bool = True,
) -> ConflictCheckResult:
    """
    Conflict Detector gate for a single field write.
    Owner mismatch / locked text / contract mismatch → blocked.
    """
    owner = FIELD_OWNERS.get(field, "")
    # Unknown fields are allowed only if not locked text
    if field in LOCKED_TEXT_FIELDS and segment is not None and is_segment_locked(segment):
        result = ConflictCheckResult(
            ok=False,
            field=field,
            owner=owner or "Translation Engine",
            requested_by=requested_by,
            detail="LOCKED text field — mutation forbidden",
        )
        if enforce:
            raise TranslationLockError(
                f"Conflict Detector: locked field {field!r} write by {requested_by!r} blocked",
                stage="conflict_detector",
                details=result.to_dict(),
            )
        return result

    if owner and requested_by and owner != requested_by:
        # Translation Engine may still write unlocked text fields
        if not (
            owner == "Translation Engine"
            and requested_by == "Translation Engine"
            and field in LOCKED_TEXT_FIELDS
            and not (segment and is_segment_locked(segment))
        ):
            try:
                assert_owner_may_write(field, requested_by)
            except Exception as exc:
                result = ConflictCheckResult(
                    ok=False,
                    field=field,
                    owner=owner,
                    requested_by=requested_by,
                    detail=str(exc),
                )
                if enforce:
                    raise ConflictDetectorError(
                        f"Conflict Detector: {requested_by!r} cannot write {field!r} (owner={owner})",
                        stage="conflict_detector",
                        details=result.to_dict(),
                    ) from exc
                return result

    if info is not None and info.get("translation_locked"):
        try:
            require_contract_versions(info)
        except ContractVersionError as exc:
            result = ConflictCheckResult(
                ok=False,
                field=field,
                owner=owner,
                requested_by=requested_by,
                detail=f"contract: {exc}",
            )
            if enforce:
                raise ConflictDetectorError(
                    f"Conflict Detector: contract mismatch blocking {field!r}",
                    stage="conflict_detector",
                    details=result.to_dict(),
                ) from exc
            return result

    return ConflictCheckResult(
        ok=True,
        field=field,
        owner=owner or requested_by,
        requested_by=requested_by,
        detail="allowed",
    )


def assert_contracts_current(info: dict[str, Any] | None) -> dict[str, int]:
    """Ensure stamped contract versions match the running system."""
    info = info or {}
    have = require_contract_versions(info) if all(
        info.get(k) is not None for k in CONTRACT_VERSIONS
    ) else {}
    for key, expected in CONTRACT_VERSIONS.items():
        if info.get(key) is not None and int(info[key]) != expected:
            raise ConflictDetectorError(
                f"stale contract {key}: have {info.get(key)} expected {expected}",
                stage="conflict_detector",
            )
    return have
