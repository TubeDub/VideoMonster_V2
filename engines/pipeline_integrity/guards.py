"""Echeloned integrity guards (TZ §3)."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.artifact_registry import ArtifactRegistry
from engines.pipeline_integrity.exceptions import (
    ArtifactIntegrityError,
    PipelineAudioIdentityError,
    PipelineIdentityError,
    PipelineIntegrityError,
    PipelineValidationError,
    RuntimeIntegrityError,
    StageSnapshotIntegrityError,
)
from engines.pipeline_integrity.segment import (
    ensure_segment_ids,
    resolve_head_segment,
    segments_by_id,
)
from engines.pipeline_integrity.tts_file_lifecycle import log_tts_lifecycle
from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref
from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage

STAGE_OWNER_MODULES: dict[str, str] = {
    "stt": "engines.stt_engine",
    "translate": "engines.translation_pipeline",
    "timing_aware_translation": "engines.timing_aware_translation",
    "validate": "engines.translation_validation",
    "locked": "engines.pipeline_integrity.translation_lock",
    "tts": "engines.tts",
    "slot_fit": "engines.timing_fit",
    "audio_timing": "engines.audio_timing_optimizer",
    "timing": "engines.timing_fit",
    "scheduler": "engines.scheduler",
    "studio_handoff": "api.studio_api",
    "bootstrap": "engines.pipeline_integrity.segment",
}


@dataclass
class GuardProfile:
    architecture_ms: float = 0.0
    runtime_ms: float = 0.0
    snapshot_ms: float = 0.0
    artifact_ms: float = 0.0
    validator_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return (
            self.architecture_ms
            + self.runtime_ms
            + self.snapshot_ms
            + self.artifact_ms
            + self.validator_ms
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "architecture_ms": round(self.architecture_ms, 3),
            "runtime_ms": round(self.runtime_ms, 3),
            "snapshot_ms": round(self.snapshot_ms, 3),
            "artifact_ms": round(self.artifact_ms, 3),
            "validator_ms": round(self.validator_ms, 3),
            "total_ms": round(self.total_ms, 3),
        }


class ArchitectureGuard:
    """Static structural rules — segment_id, identity, index-free linking."""

    @staticmethod
    def check(segments_data: list[dict[str, Any]], *, stage: str) -> None:
        if not segments_data:
            raise PipelineIdentityError(
                "segments_data is empty",
                stage=stage,
            )
        # Hotfix: resegment legacy path sometimes kept the parent UUID on
        # both halves — mint fresh ids instead of crashing the whole run.
        from engines.pipeline_integrity.segment import ensure_segment_ids

        before = [str(r.get("segment_id") or "") for r in segments_data]
        ensure_segment_ids(segments_data)
        after = [str(r.get("segment_id") or "") for r in segments_data]
        repaired = sum(1 for a, b in zip(before, after) if a != b)
        if repaired:
            import logging

            logging.getLogger("tubedub.pipeline_integrity").warning(
                "[ArchitectureGuard] repaired %d duplicate/missing segment_id(s) "
                "at stage=%s",
                repaired,
                stage,
            )

        ids: list[str] = []
        for i, row in enumerate(segments_data):
            sid = str(row.get("segment_id") or "").strip()
            if not sid:
                raise PipelineIdentityError(
                    f"segment at row {i} missing segment_id",
                    stage=stage,
                    details={"index": i},
                )
            ids.append(sid)
        if len(ids) != len(set(ids)):
            raise PipelineIdentityError(
                "duplicate segment_id detected",
                stage=stage,
                details={"count": len(ids), "unique": len(set(ids))},
            )


class RuntimeIntegrityGuard:
    """In-memory collection invariants."""

    @staticmethod
    def check(
        segments_data: list[dict[str, Any]],
        timing_map: list[Any] | None,
        *,
        stage: str,
        require_tts: bool = False,
        task_info: dict[str, Any] | None = None,
        resolve_audio=None,
    ) -> None:
        by_id = segments_by_id(segments_data)
        ArchitectureGuard.check(segments_data, stage=stage)

        if timing_map is not None and len(timing_map) < len(segments_data):
            raise RuntimeIntegrityError(
                f"timing_map shorter than segments_data ({len(timing_map)} < {len(segments_data)})",
                stage=stage,
            )

        for row in segments_data:
            sid = str(row["segment_id"])
            if row.get("merged_into") is not None or row.get("merged_into_id"):
                head = resolve_head_segment(row, by_id, segments_data)
                if head is row:
                    raise RuntimeIntegrityError(
                        f"segment {sid} merge pointer invalid",
                        stage=stage,
                        details={"merged_into_id": row.get("merged_into_id"), "merged_into": row.get("merged_into")},
                    )
                if str(head.get("segment_id") or "") == sid:
                    raise RuntimeIntegrityError(
                        f"segment {sid} merged_into self",
                        stage=stage,
                    )

            if require_tts and row.get("merged_into") is None and not row.get("merged_into_id"):
                if row.get("tts_status") == "failed" or row.get("status") == "failed":
                    continue
                try:
                    from engines.pipeline_integrity.slot_budget import segment_tts_exempt

                    if segment_tts_exempt(row):
                        continue
                except Exception:
                    if row.get("tts_blocked") or row.get("skip_tts"):
                        continue
                fname = resolve_segment_audio_ref(row)
                if not fname:
                    raise RuntimeIntegrityError(
                        f"active segment {sid} missing TTS file",
                        stage=stage,
                    )
                if resolve_audio is not None:
                    path = resolve_audio(fname, task_info=task_info)
                    exists = path.is_file()
                    # P3.1: try full stored relative path + recovery before hard fail
                    if not exists:
                        for key in ("file", "tts_file_path", "runtime_registry_path"):
                            raw = row.get(key)
                            if not raw:
                                continue
                            try:
                                alt = resolve_audio(str(raw), task_info=task_info)
                            except TypeError:
                                alt = resolve_audio(str(raw))
                            if Path(alt).is_file():
                                path = Path(alt)
                                exists = True
                                break
                    if not exists:
                        try:
                            from engines.pipeline_integrity.runtime_recovery import (
                                recover_missing_audio,
                            )

                            info = dict(task_info or {})
                            info.setdefault("segments_data", segments_data)
                            recovered = recover_missing_audio(row, info)
                            if recovered.recovered and Path(recovered.path).is_file():
                                path = Path(recovered.path)
                                exists = True
                        except Exception:
                            pass
                    task_id = str((task_info or {}).get("task_id") or "")
                    log_tts_lifecycle(
                        task_id or None,
                        event="integrity_check",
                        segment_id=sid,
                        segment_index=row.get("index"),
                        filename=fname,
                        path=path,
                        stage=stage,
                        exists=exists,
                        success=exists,
                        detail=(
                            f"tts_file_path={Path(str(row.get('tts_file_path') or '')).name or '-'}"
                            f" file={Path(str(row.get('file') or '')).name or '-'}"
                        ),
                    )
                    if not exists:
                        raise RuntimeIntegrityError(
                            f"TTS file not found for segment {sid}: {fname}",
                            stage=stage,
                        )


class StageSnapshotGuard:
    """Verify stage mutations against whitelist — no hidden heuristics."""

    @staticmethod
    def diff_violations(
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        *,
        stage: str,
        mutator_module: str | None = None,
    ) -> list[dict[str, Any]]:
        from engines.pipeline_integrity.translation_lock import (
            LOCKED_TEXT_FIELDS,
            is_segment_locked,
        )

        allowed = sorted(allowed_fields_for_stage(stage))
        allowed_set = set(allowed)
        owner = mutator_module or STAGE_OWNER_MODULES.get(stage, f"stage:{stage}")
        violations: list[dict[str, Any]] = []
        if len(before) != len(after):
            violations.append(
                {
                    "segment_id": "",
                    "field": "segment_count",
                    "old_value": len(before),
                    "new_value": len(after),
                    "stage": stage,
                    "allowed_mutations": allowed,
                    "mutator_module": owner,
                    "message": f"segment count changed {len(before)} -> {len(after)}",
                }
            )
            return violations

        for i, (b, a) in enumerate(zip(before, after)):
            sid = str(a.get("segment_id") or b.get("segment_id") or i)
            locked = is_segment_locked(b) or is_segment_locked(a)
            all_keys = set(b.keys()) | set(a.keys())
            for key in sorted(all_keys):
                old_val = b.get(key)
                new_val = a.get(key)
                if old_val == new_val:
                    continue
                if locked and key in LOCKED_TEXT_FIELDS:
                    violations.append(
                        {
                            "segment_id": sid,
                            "field": key,
                            "old_value": old_val,
                            "new_value": new_val,
                            "stage": stage,
                            "allowed_mutations": allowed,
                            "mutator_module": owner,
                            "message": (
                                f"TRANSLATION_LOCK: segment {sid}: "
                                f"disallowed text mutation of {key!r} at stage {stage!r}"
                            ),
                        }
                    )
                    continue
                if key not in allowed_set:
                    violations.append(
                        {
                            "segment_id": sid,
                            "field": key,
                            "old_value": old_val,
                            "new_value": new_val,
                            "stage": stage,
                            "allowed_mutations": allowed,
                            "mutator_module": owner,
                            "message": (
                                f"segment {sid}: disallowed mutation of {key!r} at stage {stage!r}"
                            ),
                        }
                    )
        return violations

    @staticmethod
    def check(
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        *,
        stage: str,
        mutator_module: str | None = None,
    ) -> None:
        from engines.pipeline_integrity.exceptions import TranslationLockError

        violations = StageSnapshotGuard.diff_violations(
            before,
            after,
            stage=stage,
            mutator_module=mutator_module,
        )
        if not violations:
            return
        first = violations[0]
        msg = str(first.get("message") or "")
        if msg.startswith("TRANSLATION_LOCK"):
            raise TranslationLockError(
                msg,
                stage=stage,
                segment_id=str(first.get("segment_id") or ""),
                field=str(first.get("field") or ""),
                old_value=first.get("old_value"),
                new_value=first.get("new_value"),
                mutator=str(first.get("mutator_module") or mutator_module or ""),
                details={"violations": violations},
            )
        raise StageSnapshotIntegrityError(
            first["message"],
            stage=stage,
            segment_id=str(first.get("segment_id") or ""),
            field=str(first.get("field") or ""),
            old_value=first.get("old_value"),
            new_value=first.get("new_value"),
            allowed_mutations=list(first.get("allowed_mutations") or []),
            mutator_module=str(first.get("mutator_module") or ""),
            details={"violations": violations},
        )


class ArtifactIntegrityGuard:
    """Register and verify TTS artifacts — No Audio Reuse."""

    def __init__(self) -> None:
        self.registry = ArtifactRegistry()

    def register_segments(
        self,
        segments_data: list[dict[str, Any]],
        *,
        resolve_path,
        task_info: dict[str, Any] | None,
        stage: str,
    ) -> None:
        for row in segments_data:
            if row.get("merged_into") is not None or row.get("merged_into_id"):
                continue
            if row.get("archived") or row.get("segment_archived"):
                continue
            fname = row.get("file")
            if not fname:
                tfp = row.get("tts_file_path")
                fname = Path(str(tfp)).name if tfp else None
            if not fname:
                continue
            sid = str(row["segment_id"])
            path = resolve_path(fname, task_info=task_info)
            if not path.is_file():
                raise ArtifactIntegrityError(
                    f"artifact missing on disk: {fname}",
                    stage=stage,
                    details={"segment_id": sid},
                )
            try:
                # Strict No-Audio-Reuse at TTS register time.
                self.registry.register(sid, path)
            except ValueError as exc:
                raise PipelineAudioIdentityError(
                    str(exc),
                    stage=stage,
                    details={"segment_id": sid, "file": Path(str(fname)).name},
                ) from exc

    def verify_all(
        self,
        segments_data: list[dict[str, Any]],
        *,
        resolve_path,
        task_info: dict[str, Any] | None,
        stage: str,
    ) -> None:
        for sid, rec in self.registry.records.items():
            path = resolve_path(rec.filename, task_info=task_info)
            if not self.registry.verify(sid, path):
                raise ArtifactIntegrityError(
                    f"SHA-256 mismatch for segment {sid} file {rec.filename}",
                    stage=stage,
                )


class PipelineValidator:
    """Final project validation before track build / studio handoff."""

    @staticmethod
    def validate(
        segments_data: list[dict[str, Any]],
        timing_map: list[Any] | None,
        *,
        stage: str,
        task_info: dict[str, Any] | None = None,
        artifact_registry: ArtifactRegistry | None = None,
        resolve_audio=None,
    ) -> dict[str, Any]:
        RuntimeIntegrityGuard.check(
            segments_data,
            timing_map,
            stage=stage,
            require_tts=True,
            task_info=task_info,
            resolve_audio=resolve_audio,
        )

        active = [
            s
            for s in segments_data
            if s.get("merged_into") is None
            and not s.get("merged_into_id")
            and not s.get("archived")
            and not s.get("segment_archived")
        ]
        try:
            from engines.pipeline_integrity.slot_budget import segment_tts_exempt

            voiceable = [s for s in active if not segment_tts_exempt(s)]
        except Exception:
            voiceable = [
                s
                for s in active
                if not (s.get("tts_blocked") or s.get("skip_tts"))
            ]
        with_file = [s for s in voiceable if resolve_segment_audio_ref(s)]
        if voiceable and not with_file:
            raise PipelineValidationError(
                "no active segments with TTS files",
                stage=stage,
            )
        if not voiceable:
            # All segments quality-blocked — not a missing-file bug
            raise PipelineValidationError(
                "all active segments TTS-blocked (translation rejected)",
                stage=stage,
                details={"tts_blocked": True, "active": len(active)},
            )

        file_names = [Path(str(resolve_segment_audio_ref(s) or "")).name for s in with_file]
        if len(file_names) != len(set(file_names)):
            raise PipelineAudioIdentityError(
                "duplicate TTS filename across active segments",
                stage=stage,
            )

        if artifact_registry:
            active_ids = {str(s["segment_id"]) for s in with_file if s.get("segment_id")}
            for s in with_file:
                sid = str(s["segment_id"])
                if sid in artifact_registry.records:
                    continue
                # Hotfix: adaptive resegment / UUID reissue mints NEW ids after
                # the initial TTS artifact register — sync missing rows from disk.
                ref = resolve_segment_audio_ref(s)
                registered = False
                if resolve_audio and ref:
                    path_obj: Path | None = None
                    try:
                        path_obj = Path(str(resolve_audio(ref, task_info)))
                    except TypeError:
                        try:
                            path_obj = Path(str(resolve_audio(ref)))
                        except Exception:
                            path_obj = None
                    except Exception:
                        path_obj = None
                    if path_obj is not None and path_obj.is_file():
                        try:
                            artifact_registry.register(
                                sid,
                                path_obj,
                                active_ids=active_ids,
                                rebind_orphans=True,
                            )
                            registered = True
                        except ValueError:
                            registered = False
                if not registered:
                    raise ArtifactIntegrityError(
                        f"segment {sid} not registered in artifact registry",
                        stage=stage,
                        details={"file": str(ref or "")},
                    )

        return {
            "active_segments": len(active),
            "with_tts": len(with_file),
            "stage": stage,
        }


@dataclass
class PipelineIntegrityCoordinator:
    """
    Orchestrates all guards. Validation is always mandatory (TZ §1.7).
    StageSnapshotGuard runs only after ProjectSession + Segment bootstrap snapshot.
    """

    task_id: str
    profile: GuardProfile = field(default_factory=GuardProfile)
    artifact_guard: ArtifactIntegrityGuard = field(default_factory=ArtifactIntegrityGuard)
    reports: list[dict[str, Any]] = field(default_factory=list)
    _snapshots: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _base_snapshot: list[dict[str, Any]] | None = None
    _guard_ready: bool = False
    _guard_skip_reason: str = "guard context not initialized"
    project_session_id: str | None = None

    def initialize_guard_context(
        self,
        *,
        project_session: Any | None = None,
        segments_data: list[dict[str, Any]] | None = None,
    ) -> bool:
        """
        Prepare StageSnapshotGuard lifecycle.
        Requires: ProjectSession exists, ≥1 Segment with segment_id, base snapshot created.
        """
        session_ok = bool(
            project_session is not None
            and getattr(project_session, "session_id", None)
        )
        rows = list(segments_data or [])
        segment_rows = [
            row for row in rows if str(row.get("segment_id") or "").strip()
        ]

        if not session_ok:
            self._guard_ready = False
            self._guard_skip_reason = "ProjectSession not initialized"
            return False
        if not segment_rows:
            self._guard_ready = False
            self._guard_skip_reason = "no Segment objects"
            return False

        self._base_snapshot = copy.deepcopy(segment_rows)
        self._guard_ready = True
        self._guard_skip_reason = ""
        self.project_session_id = str(project_session.session_id)
        self.reports.append(
            {
                "stage": "bootstrap",
                "snapshot_guard": "ready",
                "segments": len(segment_rows),
                "project_session_id": self.project_session_id,
            }
        )
        try:
            from engines.pipeline_integrity.passive_openddf import ensure_session, observe_guard_context_ready

            ensure_session(self.task_id)
            observe_guard_context_ready(self.task_id, segments=len(segment_rows))
        except ImportError:
            pass
        return True

    def is_snapshot_guard_ready(self) -> bool:
        return (
            self._guard_ready
            and self._base_snapshot is not None
            and len(self._base_snapshot) > 0
        )

    def assign_segment_ids(self, segments_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        t0 = time.perf_counter()
        ensure_segment_ids(segments_data)
        ArchitectureGuard.check(segments_data, stage="bootstrap")
        self.profile.architecture_ms += (time.perf_counter() - t0) * 1000.0
        return segments_data

    def begin_stage(self, stage: str, segments_data: list[dict[str, Any]]) -> None:
        if self.is_snapshot_guard_ready():
            self._snapshots[stage] = copy.deepcopy(segments_data)
        try:
            from engines.pipeline_integrity.passive_openddf import ensure_session, observe_stage_begin

            ensure_session(self.task_id)
            observe_stage_begin(self.task_id, stage)
        except ImportError:
            pass

    def end_stage(
        self,
        stage: str,
        segments_data: list[dict[str, Any]],
        *,
        timing_map: list[Any] | None = None,
        check_mutations: bool = True,
    ) -> None:
        t0 = time.perf_counter()
        before = self._snapshots.pop(stage, None)
        if check_mutations and before is not None and self.is_snapshot_guard_ready():
            task_info: dict[str, Any] | None = None
            try:
                from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

                with STATE_LOCK:
                    task = AUTO_TASKS.get(self.task_id)
                    if task:
                        task_info = dict(task.get("info") or {})
            except ImportError:
                pass
            try:
                from engines.pipeline_integrity.passive_openddf import register_stage_snapshots

                register_stage_snapshots(self.task_id, before, segments_data)
            except ImportError:
                pass
            from engines.pipeline_integrity.openddf_diagnostics import guard_check_with_diagnostics

            guard_check_with_diagnostics(
                before,
                segments_data,
                stage=stage,
                mutator_module=STAGE_OWNER_MODULES.get(stage),
                task_id=self.task_id,
                task_info=task_info,
            )
        elif check_mutations and not self.is_snapshot_guard_ready():
            self.reports.append(
                {
                    "stage": stage,
                    "snapshot_guard": "skipped",
                    "reason": self._guard_skip_reason or "guard not ready",
                }
            )
        self.profile.snapshot_ms += (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        RuntimeIntegrityGuard.check(segments_data, timing_map, stage=stage)
        self.profile.runtime_ms += (time.perf_counter() - t1) * 1000.0

        self.reports.append({"stage": stage, "status": "ok", "segments": len(segments_data)})

    def register_tts_artifacts(
        self,
        segments_data: list[dict[str, Any]],
        *,
        resolve_path,
        task_info: dict[str, Any] | None,
    ) -> None:
        t0 = time.perf_counter()
        self.artifact_guard.register_segments(
            segments_data,
            resolve_path=resolve_path,
            task_info=task_info,
            stage="tts",
        )
        self.profile.artifact_ms += (time.perf_counter() - t0) * 1000.0

    def validate_pipeline(
        self,
        segments_data: list[dict[str, Any]],
        timing_map: list[Any] | None,
        *,
        stage: str,
        task_info: dict[str, Any] | None = None,
        resolve_audio=None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        # Re-sync artifact registry after post-TTS resegment / UUID repair.
        # Orphan rebind: file still bound to archived parent id → child.
        if resolve_audio is not None:
            try:

                def _resolve(fname, task_info=None):
                    try:
                        return Path(resolve_audio(fname, task_info))
                    except TypeError:
                        return Path(resolve_audio(fname))

                active_ids = {
                    str(row["segment_id"])
                    for row in segments_data
                    if row.get("segment_id")
                    and row.get("merged_into") is None
                    and not row.get("merged_into_id")
                    and not row.get("archived")
                    and not row.get("segment_archived")
                }
                for row in segments_data:
                    if row.get("merged_into") is not None or row.get("merged_into_id"):
                        continue
                    if row.get("archived") or row.get("segment_archived"):
                        continue
                    fname = resolve_segment_audio_ref(row)
                    if not fname:
                        continue
                    sid = str(row["segment_id"])
                    path = _resolve(fname, task_info)
                    if not path.is_file():
                        continue
                    try:
                        self.artifact_guard.registry.register(
                            sid,
                            path,
                            active_ids=active_ids,
                            rebind_orphans=True,
                        )
                    except ValueError:
                        # Two active segments share a file — validator raises next.
                        pass
            except Exception as exc:
                import logging

                logging.getLogger("tubedub.pipeline_integrity").warning(
                    "[PipelineIntegrity] artifact re-sync before %s: %s",
                    stage,
                    exc,
                )
        result = PipelineValidator.validate(
            segments_data,
            timing_map,
            stage=stage,
            task_info=task_info,
            artifact_registry=self.artifact_guard.registry,
            resolve_audio=resolve_audio,
        )
        self.profile.validator_ms += (time.perf_counter() - t0) * 1000.0
        self.reports.append({"stage": stage, "validation": result})
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "profile": self.profile.to_dict(),
            "reports": list(self.reports),
            "artifacts": self.artifact_guard.registry.to_dict(),
            "snapshot_guard_ready": self.is_snapshot_guard_ready(),
            "snapshot_guard_skip_reason": self._guard_skip_reason,
            "project_session_id": self.project_session_id,
            "base_snapshot_segments": len(self._base_snapshot or []),
        }


def validation_always_enabled() -> bool:
    """TZ §1.7 — validators cannot be disabled."""
    return True


def enforce_or_raise(exc: PipelineIntegrityError) -> None:
    """No hidden heuristics — always raise (TZ §1.8)."""
    raise exc
