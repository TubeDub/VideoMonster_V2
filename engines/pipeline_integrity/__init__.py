"""Pipeline data integrity contract — public API (Stage 3A.1)."""

from engines.pipeline_integrity.artifact_registry import ArtifactRegistry, sha256_file
from engines.pipeline_integrity.exceptions import (
    ArtifactIntegrityError,
    PipelineAudioIdentityError,
    PipelineIdentityError,
    PipelineIntegrityError,
    PipelineValidationError,
    RuntimeIntegrityError,
    StageSnapshotIntegrityError,
)
from engines.pipeline_integrity.guards import (
    ArchitectureGuard,
    ArtifactIntegrityGuard,
    GuardProfile,
    PipelineIntegrityCoordinator,
    PipelineValidator,
    RuntimeIntegrityGuard,
    StageSnapshotGuard,
    enforce_or_raise,
    validation_always_enabled,
)
from engines.pipeline_integrity.rollback import StageTransaction, run_stage_atomic
from engines.pipeline_integrity.segment import (
    Segment,
    ensure_segment_ids,
    new_segment_id,
    resolve_head_segment,
    segments_by_id,
)
from engines.pipeline_integrity.stage_contracts import (
    STAGE_ALLOWED_MUTATIONS,
    allowed_fields_for_stage,
)

from engines.pipeline_integrity.openddf_diagnostics import (
    enrich_stage_snapshot_error,
    guard_check_with_diagnostics,
    release_summary_from_exc,
)
from engines.pipeline_integrity.passive_openddf import (
    attach_passive_metadata,
    capture_pipeline_exception,
    ensure_diagnostic_archive,
    ensure_session,
    get_session,
    observe_guard_context_ready,
    observe_stage_begin,
    passive_metadata,
    start_diagnostic_run,
)

__all__ = [
    "ArchitectureGuard",
    "ArtifactIntegrityError",
    "ArtifactIntegrityGuard",
    "ArtifactRegistry",
    "GuardProfile",
    "PipelineAudioIdentityError",
    "PipelineIdentityError",
    "PipelineIntegrityCoordinator",
    "PipelineIntegrityError",
    "PipelineValidationError",
    "PipelineValidator",
    "RuntimeIntegrityError",
    "RuntimeIntegrityGuard",
    "Segment",
    "StageSnapshotGuard",
    "StageSnapshotIntegrityError",
    "StageTransaction",
    "STAGE_ALLOWED_MUTATIONS",
    "allowed_fields_for_stage",
    "ensure_segment_ids",
    "enforce_or_raise",
    "enrich_stage_snapshot_error",
    "attach_passive_metadata",
    "capture_pipeline_exception",
    "ensure_diagnostic_archive",
    "ensure_session",
    "get_session",
    "guard_check_with_diagnostics",
    "observe_guard_context_ready",
    "observe_stage_begin",
    "passive_metadata",
    "start_diagnostic_run",
    "new_segment_id",
    "release_summary_from_exc",
    "resolve_head_segment",
    "run_stage_atomic",
    "segments_by_id",
    "sha256_file",
    "validation_always_enabled",
]
