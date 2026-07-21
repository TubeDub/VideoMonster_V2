"""Pipeline integrity exception hierarchy — fail-fast contract (TZ §2)."""

from __future__ import annotations

import json
from typing import Any


def _repr_value(value: Any, *, limit: int = 200) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


class PipelineIntegrityError(Exception):
    """Base integrity violation — pipeline must not continue with damaged data."""

    code: str = "pipeline_integrity"

    def __init__(
        self,
        message: str,
        *,
        stage: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.details = details or {}


class PipelineIdentityError(PipelineIntegrityError):
    """ID -> Text -> TTS -> Timing chain broken."""

    code = "pipeline_identity"


class IdentityMismatchError(PipelineIdentityError):
    """v2.0 IdentityGuard chain mismatch — abort pipeline."""

    code = "identity_mismatch"


class SegmentImmutabilityError(PipelineIdentityError):
    """PSA3 — text move/swap between existing segment_id values is forbidden."""

    code = "segment_immutability"


class RevisionManagerError(PipelineIdentityError):
    """PSA5 — revision UUID / in-place mutate / sidecar mismatch."""

    code = "revision_manager"


class PipelineAudioIdentityError(PipelineIntegrityError):
    """One TTS file bound to multiple segment_id values (No Audio Reuse)."""

    code = "pipeline_audio_identity"


class RuntimeIntegrityError(PipelineIntegrityError):
    """In-memory segment collection violates runtime invariants."""

    code = "runtime_integrity"


class StageSnapshotIntegrityError(PipelineIntegrityError):
    """Stage mutation outside allowed whitelist."""

    code = "stage_snapshot_integrity"

    def __init__(
        self,
        message: str,
        *,
        stage: str = "",
        segment_id: str = "",
        field: str = "",
        old_value: Any = None,
        new_value: Any = None,
        allowed_mutations: list[str] | None = None,
        mutator_module: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        merged.setdefault("segment_id", segment_id)
        merged.setdefault("field", field)
        merged.setdefault("old_value", old_value)
        merged.setdefault("new_value", new_value)
        merged.setdefault("allowed_mutations", list(allowed_mutations or []))
        merged.setdefault("mutator_module", mutator_module)
        super().__init__(message, stage=stage, details=merged)
        self.segment_id = segment_id
        self.field = field
        self.old_value = old_value
        self.new_value = new_value
        self.allowed_mutations = list(allowed_mutations or [])
        self.mutator_module = mutator_module

    def format_user_reason(self) -> str:
        """Short user-facing reason (not generic pipeline error)."""
        field = self.field or (self.details or {}).get("field") or "?"
        sid = self.segment_id or (self.details or {}).get("segment_id") or "?"
        return (
            f"Недопустимое изменение поля «{field}» на этапе {self.stage or '?'} "
            f"(сегмент {sid})."
        )

    def format_diagnostic_block(self) -> str:
        """Technical block — delegates to OpenDDF v1.3 when enriched."""
        openddf = (self.details or {}).get("openddf")
        if openddf and openddf.get("developer_block"):
            return str(openddf["developer_block"])
        allowed = self.allowed_mutations or (self.details or {}).get("allowed_mutations") or []
        lines = [
            f"Stage: {self.stage or '?'}",
            f"Exception:\nStageSnapshotIntegrityError",
            f"Error code:\n{self.code.upper()}",
            f"segment_id: {self.segment_id or (self.details or {}).get('segment_id', '?')}",
            f"field:\n{self.field or (self.details or {}).get('field', '?')}",
            f"previous_value:\n{_repr_value(self.old_value if self.field else (self.details or {}).get('old_value'))}",
            f"new_value:\n{_repr_value(self.new_value if self.field else (self.details or {}).get('new_value'))}",
            "allowed_mutations:\n" + ", ".join(sorted(str(x) for x in allowed)) if allowed else "allowed_mutations:\n(none)",
            f"mutator_module:\n{self.mutator_module or (self.details or {}).get('mutator_module') or 'unknown'}",
            f"Reason:\n{str(self)}",
            "Pipeline:\nSTOPPED",
        ]
        violations = (self.details or {}).get("violations") or []
        if len(violations) > 1:
            lines.append(f"additional_violations: {len(violations) - 1}")
        return "\n".join(lines)


class ArtifactIntegrityError(PipelineIntegrityError):
    """Artifact registry / SHA-256 verification failed."""

    code = "artifact_integrity"


class PipelineValidationError(PipelineIntegrityError):
    """Final pipeline validator rejected project state."""

    code = "pipeline_validation"


class PipelineStateError(PipelineIntegrityError):
    """Illegal or reverse pipeline state transition."""

    code = "pipeline_state"

    def __init__(
        self,
        message: str,
        *,
        stage: str = "",
        from_state: str = "",
        to_state: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        if from_state:
            merged.setdefault("from_state", from_state)
        if to_state:
            merged.setdefault("to_state", to_state)
        super().__init__(message, stage=stage or "pipeline_state", details=merged)
        self.from_state = from_state
        self.to_state = to_state


class ContractVersionError(PipelineIntegrityError):
    """Contract version missing or mismatched."""

    code = "contract_version"


class TranslationLockError(PipelineIntegrityError):
    """Attempt to mutate locked translation text after TRANSLATION LOCK."""

    code = "translation_lock"

    def __init__(
        self,
        message: str,
        *,
        stage: str = "translation_lock",
        segment_id: str = "",
        field: str = "",
        old_value: Any = None,
        new_value: Any = None,
        mutator: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        merged.setdefault("segment_id", segment_id)
        merged.setdefault("field", field)
        merged.setdefault("old_value", old_value)
        merged.setdefault("new_value", new_value)
        merged.setdefault("mutator", mutator)
        super().__init__(message, stage=stage, details=merged)
        self.segment_id = segment_id
        self.field = field
        self.old_value = old_value
        self.new_value = new_value
        self.mutator = mutator


class ArchitectureViolation(PipelineIntegrityError):
    """MASTER TZ v3.0 — explicit architecture boundary breach (no silent fix).

    Raised for ownership, LOCK, contract, or FSM violations when a dedicated
    typed error is not already more specific. Prefer subclassing for domains;
    this type is the TZ-named catch-all for governance and tests.
    """

    code = "architecture_violation"

    def __init__(
        self,
        message: str,
        *,
        stage: str = "architecture",
        rule: str = "",
        segment_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        if rule:
            merged.setdefault("rule", rule)
        if segment_id:
            merged.setdefault("segment_id", segment_id)
        super().__init__(message, stage=stage, details=merged)
        self.rule = rule
        self.segment_id = segment_id


# Alias: LOCK text mutations are architecture violations under TZ naming.
ArchitectureViolation.TranslationLock = TranslationLockError  # type: ignore[attr-defined]
