"""
TubeDub Passive OpenDDF integration (v0.1).

Observes pipeline execution, collects diagnostics, writes reports.
MUST NOT modify pipeline objects, auto-fix data, or alter control flow.
"""

from __future__ import annotations

import json
import logging
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from openddf import TimelineTracker, __version__ as OPENDDF_SDK_VERSION
from openddf.environment import collect_environment_info
from openddf.utils import filter_sensitive_data

logger = logging.getLogger("tubedub.passive_openddf")

_MODE = "passive"
_LOCK = Lock()

DIAGNOSTIC_ZIP_REASON_LABELS: dict[str, str] = {
    "archive_not_created": "архив не создан",
    "write_error": "ошибка записи",
    "path_not_found": "путь отсутствует",
    "registration_error": "ошибка регистрации",
    "api_error": "ошибка API",
    "permission_denied": "недостаточно прав доступа",
    "task_not_found": "задача не найдена",
    "zip_generation_failed": "ошибка записи",
    "no_session": "ошибка регистрации",
}

# Pipeline display label -> TubeDub StageSnapshotGuard stage key (None = timeline only).
PIPELINE_STAGE_GUARD_KEY: dict[str, str | None] = {
    "Audio Extraction": None,
    "Source Separation": None,
    "STT": "stt",
    "Translation": "translate",
    "Timing-Aware Translation": "timing_aware_translation",
    "Timing Engine": "timing",
    "Timing": "timing",
    "slot_fit": "slot_fit",
    "TTS": "tts",
    "Audio Mix": None,
    "FFmpeg Render": None,
    "Video Merge": None,
}


def resolve_output_dir(
    *,
    task_id: str = "",
    task_info: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    session_dir = (task_info or {}).get("session_dir")
    if session_dir:
        return Path(str(session_dir)).parent.parent
    return Path("output")


class PassiveOpenDDFSession:
    """Per-task passive DiagnosticContext — Run ID, timeline, read-only artifacts."""

    def __init__(self, run_id: str, output_dir: Path) -> None:
        self.run_id = run_id
        self.task_id = run_id
        self.output_dir = Path(output_dir)
        self.timeline = TimelineTracker()
        self.mode = _MODE
        self.sdk_version = OPENDDF_SDK_VERSION
        self.last_artifacts: dict[str, str] = {}
        self.last_zip_path: str | None = None
        self._snapshot_before: list[dict[str, Any]] | None = None
        self._snapshot_after: list[dict[str, Any]] | None = None
        self.timeline.add_event(
            "diagnostic_context_start",
            "OK",
            {
                "run_id": run_id,
                "mode": _MODE,
                "sdk": OPENDDF_SDK_VERSION,
                "integration": "TubeDub-Passive-v0.1",
            },
        )

    def record(
        self,
        event_name: str,
        status: str = "OK",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.timeline.add_event(event_name, status, metadata)

    def register_segment_snapshots(
        self,
        before: list[dict[str, Any]] | None,
        after: list[dict[str, Any]] | None,
    ) -> None:
        """Store latest segment snapshots for crash dumps (copies not taken — read-only at dump)."""
        self._snapshot_before = before
        self._snapshot_after = after

    def stage_begin(self, stage: str) -> None:
        guard_key = PIPELINE_STAGE_GUARD_KEY.get(stage, stage)
        self.record(
            "stage_begin",
            "OK",
            {"stage": stage, "guard_stage": guard_key, "snapshot_guard": guard_key is not None},
        )

    def stage_check_start(self, stage: str) -> None:
        self.record("stage_snapshot_check", "OK", {"stage": stage})

    def stage_complete(self, stage: str) -> None:
        self.record("stage_complete", "OK", {"stage": stage})

    def stage_failed(self, stage: str, *, error_type: str, field: str = "") -> None:
        meta: dict[str, Any] = {"stage": stage, "error_type": error_type}
        if field:
            meta["field"] = field
        self.record("stage_snapshot_integrity_failed", "FAILED", meta)

    def merge_runtime_events(self, task_info: dict[str, Any] | None) -> None:
        for row in list((task_info or {}).get("runtime_diagnostics") or []):
            stage = row.get("stage") or "?"
            dur = row.get("duration_ms")
            suffix = f" ({dur}ms)" if dur is not None else ""
            self.record(
                f"runtime_{stage}_completed",
                "OK",
                {
                    "stage": stage,
                    "duration_ms": dur,
                    "message": f"{stage} completed{suffix}",
                    "timestamp": row.get("timestamp"),
                },
            )

    def export_pipeline_log_text(self, *, task_id: str, base_dir: Path) -> str:
        lines = [self.timeline.export_text()]
        log_candidates = [
            base_dir / "logs" / "tubedub.log",
            base_dir / "dub_segment_log.txt",
            base_dir / "dub_timing_fit_log.txt",
        ]
        for log_path in log_candidates:
            if not log_path.is_file():
                continue
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_lines = content.splitlines()
            matched = [ln for ln in file_lines if task_id and task_id in ln]
            if matched:
                lines.append(f"--- {log_path.name} (task={task_id}) ---")
                lines.extend(matched[-500:])
            else:
                tail = file_lines[-200:]
                if tail:
                    lines.append(f"--- {log_path.name} (tail) ---")
                    lines.extend(tail)
        return "\n".join(lines) if lines else f"(passive pipeline log — task {task_id})"

    def _write_bundle_files(
        self,
        out_dir: Path,
        *,
        stage: str,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        diff_payload: dict[str, Any],
        developer_payload: dict[str, Any],
        stacktrace: str,
        exception_info: dict[str, Any] | None = None,
        extra_files: dict[str, Any] | None = None,
        semantic_report: dict[str, Any] | None = None,
    ) -> dict[str, Path]:
        safe_before = filter_sensitive_data(before)
        safe_after = filter_sensitive_data(after)
        safe_diff = filter_sensitive_data(diff_payload)
        safe_dev = filter_sensitive_data(developer_payload)
        environment = filter_sensitive_data(collect_environment_info())

        paths: dict[str, Path] = {
            "snapshot_before": out_dir / "snapshot_before.json",
            "snapshot_after": out_dir / "snapshot_after.json",
            "snapshot_diff": out_dir / "snapshot_diff.json",
            "report": out_dir / "report.json",
            "pipeline_log": out_dir / "pipeline.log",
            "stacktrace": out_dir / "stacktrace.txt",
            "environment": out_dir / "environment.json",
            "diagnostics_dir": out_dir,
        }

        paths["snapshot_before"].write_text(
            json.dumps(safe_before, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["snapshot_after"].write_text(
            json.dumps(safe_after, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["snapshot_diff"].write_text(
            json.dumps(safe_diff, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["pipeline_log"].write_text(
            self.export_pipeline_log_text(task_id=self.run_id, base_dir=self.output_dir),
            encoding="utf-8",
        )
        paths["stacktrace"].write_text(
            stacktrace or "(no stacktrace captured)",
            encoding="utf-8",
        )
        paths["environment"].write_text(
            json.dumps(environment, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        if extra_files:
            for name, content in extra_files.items():
                path = out_dir / name
                if isinstance(content, (dict, list)):
                    path.write_text(
                        json.dumps(filter_sensitive_data(content), ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8",
                    )
                else:
                    path.write_text(str(content), encoding="utf-8")
                paths[name.replace(".json", "").replace(".", "_")] = path

        report_doc = {
            "framework": "OpenDDF",
            "integration": "TubeDub-Passive",
            "mode": _MODE,
            "sdk_version": OPENDDF_SDK_VERSION,
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task_id": self.task_id,
            "stage": stage,
            "developer": safe_dev,
            "snapshot_diff": safe_diff,
            "exception": exception_info or {},
            "timeline_event_count": len(self.timeline.get_events()),
        }
        if semantic_report:
            report_doc["semantic_validation"] = filter_sensitive_data(semantic_report)
        paths["report"].write_text(
            json.dumps(report_doc, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return paths

    def _zip_bundle(self, paths: dict[str, Path]) -> str:
        zip_dir = self.output_dir / "diagnostics"
        zip_dir.mkdir(parents=True, exist_ok=True)
        zip_path = zip_dir / f"diagnostic_{self.run_id}.zip"
        if zip_path.is_file():
            try:
                zip_path.unlink()
            except (PermissionError, OSError):
                # Windows: prior zip may be locked by a running process. Never lose
                # diagnostics or crash the pipeline — write to a unique fallback name.
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                zip_path = zip_dir / f"diagnostic_{self.run_id}_{ts}.zip"
        extra_paths: list[Path] = []
        app_root = Path(__file__).resolve().parent.parent.parent
        task_diag = app_root / "output" / "diagnostics" / self.run_id
        for fname in (
            "audio_extraction_report.json",
            "ffmpeg_stderr.log",
            "traceback.txt",
            # Unified Language Validation diagnostics (TZ P0)
            "language_validator.log",
            "confidence_scores.json",
            "recovery_trace.json",
            "decision_trace.json",
        ):
            candidate = task_diag / fname
            if candidate.is_file():
                extra_paths.append(candidate)
        ddf = app_root / "output" / f"ddf_{self.run_id}.json"
        if ddf.is_file():
            extra_paths.append(ddf)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for key, path in paths.items():
                if key == "diagnostics_dir" or not path.is_file():
                    continue
                zf.write(path, path.name)
            seen = {p.name for p in paths.values() if p.is_file()}
            for extra in extra_paths:
                if not extra.is_file():
                    continue
                arcname = "ddf_report.json" if extra.name.startswith("ddf_") else extra.name
                if arcname not in seen:
                    zf.write(extra, arcname)
                    seen.add(arcname)
        return str(zip_path.resolve())

    def persist_segment_bundle(
        self,
        *,
        stage: str,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        diff_payload: dict[str, Any],
        developer_payload: dict[str, Any],
        stacktrace: str = "",
        exception_info: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Write read-only diagnostic files; does not touch pipeline segment data."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        out_dir = (
            self.output_dir
            / "diagnostics"
            / self.task_id
            / f"passive_{stage}_{ts}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        paths = self._write_bundle_files(
            out_dir,
            stage=stage,
            before=before,
            after=after,
            diff_payload=diff_payload,
            developer_payload=developer_payload,
            stacktrace=stacktrace,
            exception_info=exception_info,
        )
        zip_path = self._zip_bundle(paths)
        artifact_paths = {k: str(v) for k, v in paths.items()}
        artifact_paths["diagnostic_zip"] = zip_path
        self.last_artifacts = artifact_paths
        self.last_zip_path = zip_path
        self.record(
            "passive_artifacts_written",
            "OK",
            {"dir": str(out_dir), "zip": zip_path},
        )
        return artifact_paths

    def persist_crash_bundle(
        self,
        exc: BaseException,
        *,
        stage: str = "unknown",
        task_info: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Generic pipeline crash — timeline + environment + optional snapshots."""
        self.merge_runtime_events(task_info)
        self.record(
            "pipeline_crash",
            "FAILED",
            {"stage": stage, "error_type": type(exc).__name__, "message": str(exc)[:500]},
        )
        before = list(self._snapshot_before or [])
        after = list(self._snapshot_after or [])
        if not before and task_info:
            segs = list(task_info.get("segments_data") or [])
            if segs:
                after = segs
        tb = traceback.format_exc()
        diff_payload = {
            "stage": stage,
            "task_id": self.task_id,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        developer_payload = attach_passive_metadata(
            {
                "error_code": getattr(exc, "code", type(exc).__name__).upper(),
                "stage": stage,
                "recovery_hint": {
                    "text": "Inspect stacktrace.txt and pipeline.log in the diagnostic archive.",
                },
            }
        )
        return self.persist_segment_bundle(
            stage=stage,
            before=before,
            after=after,
            diff_payload=diff_payload,
            developer_payload=developer_payload,
            stacktrace=tb,
            exception_info={
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )

    def persist_semantic_validation_bundle(
        self,
        exc: BaseException,
        *,
        stage: str = "semantic_validation",
        task_info: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """
        Rich OpenDDF bundle for SemanticValidationError (TZ §1–§8).
        """
        from engines.semantic_meaning import SemanticValidationError
        from engines.pipeline_integrity.semantic_validation_openddf import (
            build_runtime_pipeline,
            build_semantic_report_json,
            build_semantic_snapshot_diff,
        )

        if not isinstance(exc, SemanticValidationError):
            return self.persist_crash_bundle(exc, stage=stage, task_info=task_info)

        self.merge_runtime_events(task_info)
        payload = dict(exc.details or {})
        before = list(payload.get("snapshot_before") or self._snapshot_before or [])
        after = list(payload.get("snapshot_after") or self._snapshot_after or [])

        if not before and task_info:
            source = task_info.get("source_segments") or []
            before = [
                {
                    "index": i,
                    "original": str(source[i] if i < len(source) else ""),
                    "raw_mt": "",
                    "semantic_output": "",
                    "final_output": "",
                }
                for i in range(len(source))
            ]
        if not after and before:
            after = list(before)

        diff_payload = build_semantic_snapshot_diff(payload)
        diff_payload["task_id"] = self.task_id
        diff_payload["stage"] = stage
        diff_payload["message"] = str(exc)

        runtime_pipeline = build_runtime_pipeline(task_info, validation_payload=payload)
        semantic_report = build_semantic_report_json(
            payload, task_id=self.task_id, stage=stage
        )
        from engines.pipeline_integrity.semantic_validation_openddf import (
            format_runtime_pipeline_block,
            summarize_runtime_pipeline,
        )

        runtime_summary = summarize_runtime_pipeline(runtime_pipeline)
        runtime_text = format_runtime_pipeline_block(runtime_pipeline)
        semantic_report["runtime_pipeline"] = runtime_pipeline
        semantic_report["runtime_pipeline_summary"] = runtime_summary
        semantic_report["runtime_pipeline_text"] = runtime_text

        tb = traceback.format_exc()
        developer_payload = attach_passive_metadata(
            {
                "error_code": exc.code,
                "stage": stage,
                "semantic_validation": semantic_report,
                "runtime_pipeline": runtime_pipeline,
                "runtime_pipeline_summary": runtime_summary,
                "runtime_pipeline_text": runtime_text,
                "recovery_hint": {
                    "text": (
                        "Inspect snapshot_diff.json (word/entity diff), "
                        "semantic_validation_report.json, runtime_pipeline.json."
                    ),
                },
            }
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        out_dir = (
            self.output_dir
            / "diagnostics"
            / self.task_id
            / f"passive_{stage}_{ts}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        paths = self._write_bundle_files(
            out_dir,
            stage=stage,
            before=before,
            after=after,
            diff_payload=diff_payload,
            developer_payload=developer_payload,
            stacktrace=tb,
            exception_info=exc.to_openddf_exception_info(),
            extra_files={
                "runtime_pipeline.json": runtime_pipeline,
                "semantic_validation_report.json": semantic_report,
            },
            semantic_report=semantic_report,
        )
        zip_path = self._zip_bundle(paths)
        artifact_paths = {k: str(v) for k, v in paths.items()}
        artifact_paths["diagnostic_zip"] = zip_path
        artifact_paths["runtime_pipeline"] = str(paths.get("runtime_pipeline_json") or out_dir / "runtime_pipeline.json")
        artifact_paths["semantic_validation_report"] = str(
            paths.get("semantic_validation_report_json") or out_dir / "semantic_validation_report.json"
        )
        self.last_artifacts = artifact_paths
        self.last_zip_path = zip_path
        self.record(
            "semantic_validation_failed",
            "FAILED",
            {
                "stage": stage,
                "segments": payload.get("problem_segment_indices") or [],
                "zip": zip_path,
            },
        )
        return artifact_paths

    def persist_project_qa_bundle(
        self,
        task_info: dict[str, Any],
        *,
        qa_report: dict[str, Any] | None = None,
        stage: str = "final_qa",
    ) -> dict[str, str]:
        """
        Write final dub QA + per-segment diagnostics (TZ §11, §13).
        Read-only — does not mutate pipeline segment data.
        """
        from engines.pipeline_integrity.semantic_validation_openddf import (
            build_runtime_pipeline,
            format_runtime_pipeline_block,
            summarize_runtime_pipeline,
        )
        from engines.segment_timing_qa import (
            build_final_dub_qa_report,
            build_openddf_segment_diagnostics,
        )

        report = qa_report or build_final_dub_qa_report(task_info)
        segment_diag = build_openddf_segment_diagnostics(task_info)
        segs = list(task_info.get("segments_data") or [])

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        out_dir = self.output_dir / "diagnostics" / self.task_id / f"passive_{stage}_{ts}"
        out_dir.mkdir(parents=True, exist_ok=True)

        runtime_pipeline = build_runtime_pipeline(task_info)
        from engines.segment_timing_qa import build_openddf_full_report

        openddf_full = task_info.get("openddf_full_report") or build_openddf_full_report(task_info)
        runtime_summary = summarize_runtime_pipeline(runtime_pipeline)
        runtime_text = format_runtime_pipeline_block(runtime_pipeline)

        qa_path = out_dir / "final_dub_qa.json"
        seg_path = out_dir / "segment_diagnostics.json"
        runtime_path = out_dir / "runtime_pipeline.json"
        openddf_path = out_dir / "openddf_full_report.json"
        qa_path.write_text(
            json.dumps(filter_sensitive_data(report), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        seg_path.write_text(
            json.dumps(
                filter_sensitive_data(segment_diag),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        runtime_path.write_text(
            json.dumps(
                filter_sensitive_data(runtime_pipeline),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        openddf_path.write_text(
            json.dumps(
                filter_sensitive_data(openddf_full),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        developer_payload = attach_passive_metadata(
            {
                "stage": stage,
                "qa_ok": report.get("ok"),
                "issue_count": report.get("issue_count"),
                "segment_count": len(segment_diag),
                "per_segment_trace": segment_diag,
                "runtime_pipeline": runtime_pipeline,
                "runtime_pipeline_summary": runtime_summary,
                "runtime_pipeline_text": runtime_text,
                "openddf_full_report": openddf_full,
            }
        )
        paths = self._write_bundle_files(
            out_dir,
            stage=stage,
            before=segs,
            after=segs,
            diff_payload={
                "final_dub_qa": report,
                "segment_diagnostics_count": len(segment_diag),
                "runtime_pipeline_summary": runtime_summary,
                "openddf_summary": openddf_full.get("summary"),
            },
            developer_payload=developer_payload,
            stacktrace="",
            extra_files={
                "runtime_pipeline.json": runtime_pipeline,
                "openddf_full_report.json": openddf_full,
            },
        )
        paths["final_dub_qa"] = qa_path
        paths["segment_diagnostics"] = seg_path
        paths["runtime_pipeline"] = runtime_path
        paths["openddf_full_report"] = openddf_path
        zip_path = self._zip_bundle(paths)
        artifact_paths = {k: str(v) for k, v in paths.items()}
        artifact_paths["diagnostic_zip"] = zip_path
        self.last_artifacts = artifact_paths
        self.last_zip_path = zip_path
        self.record(
            "final_qa_bundle",
            "OK" if report.get("ok") else "WARN",
            {"issues": report.get("issue_count"), "dir": str(out_dir), "zip": zip_path},
        )
        return artifact_paths


_REGISTRY: dict[str, PassiveOpenDDFSession] = {}


def get_session(task_id: str) -> PassiveOpenDDFSession | None:
    with _LOCK:
        return _REGISTRY.get(task_id)


def ensure_session(
    task_id: str,
    *,
    output_dir: Path | None = None,
    task_info: dict[str, Any] | None = None,
) -> PassiveOpenDDFSession | None:
    if not task_id:
        return None
    base = resolve_output_dir(task_id=task_id, task_info=task_info, output_dir=output_dir)
    with _LOCK:
        session = _REGISTRY.get(task_id)
        if session is None:
            session = PassiveOpenDDFSession(task_id, base)
            _REGISTRY[task_id] = session
        return session


def clear_session(task_id: str) -> None:
    with _LOCK:
        _REGISTRY.pop(task_id, None)


def start_diagnostic_run(
    task_id: str,
    *,
    output_dir: Path | None = None,
    task_info: dict[str, Any] | None = None,
) -> PassiveOpenDDFSession | None:
    """TZ §2 — DiagnosticContext at dub run start (Run ID + timeline)."""
    session = ensure_session(task_id, output_dir=output_dir, task_info=task_info)
    if session:
        session.record("dub_run_start", "OK", {"run_id": task_id})
        logger.info("[Passive-OpenDDF] run started task=%s run_id=%s", task_id, task_id)
    return session


def diagnostic_zip_status_fields(
    zip_path: str | None,
    *,
    reason_code: str | None = None,
    pending: bool = False,
) -> dict[str, Any]:
    """Public ZIP status for API/UI: created | creating | failed + reason."""
    if zip_path and Path(zip_path).is_file():
        return {
            "diagnostic_zip": zip_path,
            "diagnostic_zip_available": True,
            "diagnostic_zip_status": "created",
            "diagnostic_zip_reason_code": None,
            "diagnostic_zip_reason": None,
        }
    if pending and not reason_code:
        return {
            "diagnostic_zip": zip_path,
            "diagnostic_zip_available": False,
            "diagnostic_zip_status": "creating",
            "diagnostic_zip_reason_code": None,
            "diagnostic_zip_reason": None,
        }
    code = reason_code or "archive_not_created"
    return {
        "diagnostic_zip": zip_path,
        "diagnostic_zip_available": False,
        "diagnostic_zip_status": "failed",
        "diagnostic_zip_reason_code": code,
        "diagnostic_zip_reason": DIAGNOSTIC_ZIP_REASON_LABELS.get(code, code),
    }


def passive_metadata(task_id: str) -> dict[str, Any]:
    session = get_session(task_id)
    if not session:
        return {}
    diag_task_dir = session.output_dir / "diagnostics" / task_id
    zip_path = session.last_zip_path
    meta = {
        "run_id": session.run_id,
        "mode": _MODE,
        "sdk_version": OPENDDF_SDK_VERSION,
        "diagnostics_dir": str(diag_task_dir) if diag_task_dir.is_dir() else None,
        "artifacts": dict(session.last_artifacts),
    }
    meta.update(diagnostic_zip_status_fields(zip_path))
    return meta


def _find_zip_on_disk(
    task_id: str,
    *,
    task_info: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> str | None:
    base = resolve_output_dir(task_id=task_id, task_info=task_info, output_dir=output_dir)
    candidates: list[Any] = []
    if task_info:
        passive = task_info.get("passive_openddf") or {}
        arts = task_info.get("openddf_artifacts") or {}
        candidates.extend(
            [
                passive.get("diagnostic_zip"),
                arts.get("diagnostic_zip"),
                task_info.get("diagnostic_zip"),
            ]
        )
    candidates.append(base / "diagnostics" / f"diagnostic_{task_id}.zip")
    task_diag_dir = base / "diagnostics" / task_id
    if task_diag_dir.is_dir():
        for path in sorted(task_diag_dir.glob("**/diagnostic_*.zip"), reverse=True):
            if path.is_file():
                candidates.append(str(path.resolve()))
    for raw in candidates:
        if not raw:
            continue
        path = Path(str(raw))
        if path.is_file():
            return str(path.resolve())
    return None


def sync_session_from_artifacts(task_id: str, artifacts: dict[str, str] | None) -> None:
    if not artifacts:
        return
    zip_path = artifacts.get("diagnostic_zip")
    if not zip_path or not Path(zip_path).is_file():
        return
    session = get_session(task_id)
    if session is None:
        return
    session.last_zip_path = str(Path(zip_path).resolve())
    session.last_artifacts = {**session.last_artifacts, **artifacts}


def publish_task_diagnostic(
    task_id: str,
    *,
    task_info: dict[str, Any] | None = None,
    artifacts: dict[str, str] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Register diagnostic ZIP for task info / status API (read-only metadata)."""
    sync_session_from_artifacts(task_id, artifacts)
    session = ensure_session(task_id, output_dir=output_dir, task_info=task_info)
    zip_path = None
    if session and session.last_zip_path and Path(session.last_zip_path).is_file():
        zip_path = session.last_zip_path
    if not zip_path:
        zip_path = _find_zip_on_disk(task_id, task_info=task_info, output_dir=output_dir)
        if zip_path and session:
            session.last_zip_path = zip_path
    meta = passive_metadata(task_id) or {
        "run_id": task_id,
        "mode": _MODE,
        "sdk_version": OPENDDF_SDK_VERSION,
        "artifacts": dict(artifacts or {}),
    }
    if zip_path:
        meta.update(diagnostic_zip_status_fields(zip_path))
        if session:
            session.last_zip_path = zip_path
        logger.info(
            "[Passive-OpenDDF] diagnostic zip registered run_id=%s path=%s",
            task_id,
            zip_path,
        )
    else:
        meta.update(diagnostic_zip_status_fields(None, reason_code="archive_not_created"))
        logger.warning(
            "[Passive-OpenDDF] diagnostic zip not registered run_id=%s reason=archive_not_created",
            task_id,
        )
    return meta


def diagnostic_status_for_task(
    task_id: str,
    task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Public diagnostic ZIP fields for status API (release + dev)."""
    info = task_info or {}
    passive = info.get("passive_openddf") or {}
    arts = info.get("openddf_artifacts") or {}
    zip_path = passive.get("diagnostic_zip") or arts.get("diagnostic_zip")
    if zip_path and Path(zip_path).is_file():
        return diagnostic_zip_status_fields(zip_path)
    found = _find_zip_on_disk(task_id, task_info=info)
    if found:
        return diagnostic_zip_status_fields(found)
    stored_status = passive.get("diagnostic_zip_status")
    stored_reason = passive.get("diagnostic_zip_reason_code")
    if stored_status == "failed" and stored_reason:
        return diagnostic_zip_status_fields(zip_path, reason_code=stored_reason)
    if stored_status == "creating":
        return diagnostic_zip_status_fields(zip_path, pending=True)
    return diagnostic_zip_status_fields(zip_path, reason_code="path_not_found")


def ensure_diagnostic_archive(
    task_id: str,
    *,
    task_info: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> str | None:
    """Return path to diagnostic ZIP; build minimal bundle if missing (TZ §5)."""
    session = ensure_session(task_id, output_dir=output_dir, task_info=task_info)
    try:
        from engines.ai_core.unified_diagnostics import save_unified_diagnostics

        if task_info:
            save_unified_diagnostics(task_id, task_info=task_info)
    except Exception:
        pass
    if session is None:
        logger.warning(
            "[Passive-OpenDDF] ensure archive failed run_id=%s reason=no_session",
            task_id,
        )
        return None
    if session.last_zip_path and Path(session.last_zip_path).is_file():
        return session.last_zip_path
    stored = _find_zip_on_disk(task_id, task_info=task_info, output_dir=output_dir)
    if stored:
        session.last_zip_path = stored
        logger.info(
            "[Passive-OpenDDF] diagnostic zip resolved from storage run_id=%s path=%s",
            task_id,
            stored,
        )
        return stored

    info = task_info or {}
    passive_meta = info.get("passive_openddf") or {}
    prior_zip = passive_meta.get("diagnostic_zip") or (info.get("openddf_artifacts") or {}).get(
        "diagnostic_zip"
    )
    if prior_zip and Path(str(prior_zip)).is_file():
        session.last_zip_path = str(prior_zip)
        return str(prior_zip)

    pipeline_error = info.get("pipeline_error") or info.get("last_pipeline_diagnostic")
    failures = info.get("pipeline_failures") or []
    if pipeline_error or failures:
        last_msg = str(pipeline_error or "")
        if failures and isinstance(failures[-1], dict):
            last_msg = str(failures[-1].get("reason") or failures[-1].get("reason_short") or last_msg)
        try:
            arts = session.persist_crash_bundle(
                RuntimeError(last_msg or "Pipeline failed"),
                stage=str(info.get("last_pipeline_stage") or "pipeline"),
                task_info=task_info,
            )
            zip_path = arts.get("diagnostic_zip")
            if zip_path and Path(zip_path).is_file():
                logger.info(
                    "[Passive-OpenDDF] diagnostic zip from prior failure run_id=%s path=%s",
                    task_id,
                    zip_path,
                )
                return zip_path
        except (PermissionError, OSError) as exc:
            logger.warning(
                "[Passive-OpenDDF] ensure archive from failure meta failed run_id=%s: %s",
                task_id,
                exc,
            )

    if task_info and (
        task_info.get("segments_data")
        or task_info.get("translation_audits")
        or task_info.get("post_tts_qa")
        or task_info.get("openddf_full_report")
    ):
        try:
            arts = session.persist_project_qa_bundle(task_info, stage="ensure_archive")
            zip_path = arts.get("diagnostic_zip")
            if zip_path and Path(zip_path).is_file():
                logger.info(
                    "[Passive-OpenDDF] diagnostic zip from task QA run_id=%s path=%s",
                    task_id,
                    zip_path,
                )
                return zip_path
        except (PermissionError, OSError) as exc:
            logger.warning(
                "[Passive-OpenDDF] ensure archive QA bundle failed run_id=%s: %s",
                task_id,
                exc,
            )

    try:
        arts = session.persist_segment_bundle(
            stage="ensure_archive",
            before=list(session._snapshot_before or []),
            after=list((task_info or {}).get("segments_data") or []),
            diff_payload={
                "stage": "ensure_archive",
                "task_id": task_id,
                "message": "On-demand diagnostic archive (no pipeline failure recorded)",
            },
            developer_payload=attach_passive_metadata(
                {
                    "stage": "ensure_archive",
                    "recovery_hint": {
                        "text": "Passive diagnostic bundle created on demand.",
                    },
                }
            ),
            stacktrace="",
            exception_info=None,
        )
    except PermissionError:
        logger.warning(
            "[Passive-OpenDDF] ensure archive failed run_id=%s reason=permission_denied",
            task_id,
        )
        return None
    except OSError as exc:
        logger.warning(
            "[Passive-OpenDDF] ensure archive failed run_id=%s reason=write_error detail=%s",
            task_id,
            exc,
        )
        return None
    zip_path = arts.get("diagnostic_zip")
    if zip_path and Path(zip_path).is_file():
        logger.info(
            "[Passive-OpenDDF] diagnostic zip created on demand run_id=%s path=%s",
            task_id,
            zip_path,
        )
        return zip_path
    logger.warning(
        "[Passive-OpenDDF] ensure archive failed run_id=%s reason=zip_generation_failed",
        task_id,
    )
    return None


def capture_pipeline_exception(
    task_id: str,
    exc: BaseException,
    *,
    stage: str = "",
    before: list[dict[str, Any]] | None = None,
    after: list[dict[str, Any]] | None = None,
    task_info: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, str]:
    """
    TZ §4 — persist traceback, timeline, environment, snapshots; re-raise is caller duty.
    """
    session = ensure_session(task_id, output_dir=output_dir, task_info=task_info)
    if session is None:
        return {}
    if before is not None:
        session.register_segment_snapshots(before, after)
    session.record(
        "unhandled_exception",
        "FAILED",
        {"stage": stage, "error_type": type(exc).__name__},
    )
    from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError

    if isinstance(exc, StageSnapshotIntegrityError):
        openddf = (exc.details or {}).get("openddf") or {}
        sync_session_from_artifacts(task_id, openddf.get("artifacts"))
        arts = dict((passive_metadata(task_id).get("artifacts") or session.last_artifacts))
        if not arts.get("diagnostic_zip"):
            logger.warning(
                "[Passive-OpenDDF] SSIE without diagnostic zip run_id=%s reason=archive_not_created",
                task_id,
            )
        return arts
    from engines.semantic_meaning import SemanticValidationError

    if isinstance(exc, SemanticValidationError):
        return session.persist_semantic_validation_bundle(
            exc,
            stage=stage or "semantic_validation",
            task_info=task_info,
        )
    return session.persist_crash_bundle(exc, stage=stage or "unknown", task_info=task_info)


def observe_guard_context_ready(task_id: str, *, segments: int = 0) -> None:
    session = get_session(task_id)
    if session:
        session.record("guard_context_ready", "OK", {"segments": segments})


def observe_stage_begin(task_id: str, stage: str) -> None:
    session = get_session(task_id)
    if session:
        session.stage_begin(stage)


def observe_stage_check(
    task_id: str,
    stage: str,
    *,
    task_info: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> PassiveOpenDDFSession | None:
    session = ensure_session(task_id, output_dir=output_dir, task_info=task_info)
    if session:
        session.merge_runtime_events(task_info)
        session.stage_check_start(stage)
    return session


def observe_stage_success(task_id: str, stage: str) -> None:
    session = get_session(task_id)
    if session:
        session.stage_complete(stage)


def observe_stage_failure(
    task_id: str,
    stage: str,
    *,
    error_type: str,
    field: str = "",
) -> None:
    session = get_session(task_id)
    if session:
        session.stage_failed(stage, error_type=error_type, field=field)


def register_stage_snapshots(
    task_id: str,
    before: list[dict[str, Any]] | None,
    after: list[dict[str, Any]] | None,
) -> None:
    session = get_session(task_id)
    if session:
        session.register_segment_snapshots(before, after)


def attach_passive_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    payload.setdefault("mode", _MODE)
    payload.setdefault("sdk_version", OPENDDF_SDK_VERSION)
    payload.setdefault("integration", "TubeDub-Passive-v0.1")
    return payload
