"""Freeze TZ P4 / P3.1 — Error taxonomy + dubbing metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.pipeline_integrity.exceptions import (
    ContractVersionError,
    PipelineAudioIdentityError,
    PipelineIdentityError,
    PipelineIntegrityError,
    PipelineStateError,
    RuntimeIntegrityError,
    TranslationLockError,
)
from engines.scheduler.errors import SchedulerError


class TranslationError(PipelineIntegrityError):
    code = "translation_error"


class TimingError(PipelineIntegrityError):
    code = "timing_error"


class TTSError(PipelineIntegrityError):
    code = "tts_error"


class StudioError(PipelineIntegrityError):
    code = "studio_error"


class MergeError(PipelineIntegrityError):
    code = "merge_error"


class IdentityError(PipelineIdentityError):
    code = "identity_error"


class SegmentOverflowError(PipelineIntegrityError):
    """Segment marked overflow after audio-only optimization exhausted."""

    code = "overflow_error"


# P3.1 §17 — Runtime Integrity taxonomy
class MissingAudioFile(PipelineIntegrityError):
    code = "missing_audio_file"


class BrokenReference(PipelineIntegrityError):
    code = "broken_reference"


class InvalidUUID(PipelineIntegrityError):
    code = "invalid_uuid"


class DuplicateUUID(PipelineIntegrityError):
    code = "duplicate_uuid"


class CorruptedWAV(PipelineIntegrityError):
    code = "corrupted_wav"


class MissingMetadata(PipelineIntegrityError):
    code = "missing_metadata"


class InvalidLifecycle(PipelineIntegrityError):
    code = "invalid_lifecycle"


class InvalidOwner(PipelineIntegrityError):
    code = "invalid_owner"


class CleanupViolation(PipelineIntegrityError):
    code = "cleanup_violation"


class RegistryMismatch(PipelineIntegrityError):
    code = "registry_mismatch"


class FSMViolation(PipelineIntegrityError):
    code = "fsm_violation"


class ContractViolation(PipelineIntegrityError):
    code = "contract_violation"


class HandoffViolation(RuntimeIntegrityError):
    code = "handoff_violation"


# Re-export for taxonomy catalog
TAXONOMY: dict[str, type[Exception]] = {
    "TranslationError": TranslationError,
    "TimingError": TimingError,
    "TTSError": TTSError,
    "SchedulerError": SchedulerError,
    "StudioError": StudioError,
    "MergeError": MergeError,
    "IdentityError": IdentityError,
    "OverflowError": SegmentOverflowError,
    "SegmentOverflowError": SegmentOverflowError,
    "ContractVersionError": ContractVersionError,
    "TranslationLockError": TranslationLockError,
    "PipelineStateError": PipelineStateError,
    "PipelineAudioIdentityError": PipelineAudioIdentityError,
    "MissingAudioFile": MissingAudioFile,
    "BrokenReference": BrokenReference,
    "InvalidUUID": InvalidUUID,
    "DuplicateUUID": DuplicateUUID,
    "CorruptedWAV": CorruptedWAV,
    "MissingMetadata": MissingMetadata,
    "InvalidLifecycle": InvalidLifecycle,
    "InvalidOwner": InvalidOwner,
    "CleanupViolation": CleanupViolation,
    "RegistryMismatch": RegistryMismatch,
    "FSMViolation": FSMViolation,
    "ContractViolation": ContractViolation,
    "HandoffViolation": HandoffViolation,
}


@dataclass
class DubMetrics:
    overlap_count: int = 0
    overflow_count: int = 0
    borrowed_time: int = 0
    stretch_percent: float = 0.0
    silence_trim: int = 0
    scheduler_iterations: int = 0
    tempo_change: float = 1.0
    crossfade_ms: int = 0
    gap_redistributed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_optimizer(cls, metrics: Any) -> "DubMetrics":
        d = metrics.to_dict() if hasattr(metrics, "to_dict") else dict(metrics or {})
        return cls(
            overlap_count=int(d.get("overlap_count") or 0),
            overflow_count=int(d.get("overflow_count") or 0),
            borrowed_time=int(d.get("borrowed_time_ms") or d.get("borrowed_time") or 0),
            stretch_percent=float(d.get("stretch_percent") or 0.0),
            silence_trim=int(d.get("silence_trim_ms") or d.get("silence_trim") or 0),
            scheduler_iterations=int(d.get("scheduler_iterations") or 0),
            tempo_change=float(d.get("tempo_change") or 1.0),
            crossfade_ms=int(d.get("crossfade_ms") or 0),
            gap_redistributed_ms=int(d.get("gap_redistributed_ms") or 0),
        )


def classify_exception(exc: BaseException) -> str:
    for name, cls in TAXONOMY.items():
        if isinstance(exc, cls):
            return name
    if isinstance(exc, PipelineIntegrityError):
        return type(exc).__name__
    return type(exc).__name__


def collect_metrics_from_info(info: dict[str, Any] | None) -> DubMetrics:
    info = info or {}
    opt = info.get("audio_timing_optimizer") or {}
    m = opt.get("metrics") or {}
    if m:
        return DubMetrics.from_optimizer(m)
    return DubMetrics(
        overflow_count=int(info.get("overflow_count") or 0),
        overlap_count=int(info.get("overlap_count") or 0),
    )


def stamp_metrics(info: dict[str, Any], metrics: DubMetrics) -> None:
    info["dub_metrics"] = metrics.to_dict()
