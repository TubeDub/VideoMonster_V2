"""P16.7 — Enriched error logging for Production Hardening."""

from __future__ import annotations

import traceback
from typing import Any

from engines.pipeline_integrity.error_recovery import plan_recovery
from engines.pipeline_integrity.error_taxonomy import classify_exception


def build_error_record(
    *,
    run_id: str,
    stage: str,
    message: str,
    exc: BaseException | None = None,
    segment_uuid: str = "",
    segment_id: str = "",
    error_code: str = "",
    diagnostic_zip: str = "",
    attempt: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Mandatory error envelope:
      Run ID, Segment UUID, Stage, Error Class, Stack Trace,
      Recovery Strategy, Diagnostic ZIP link.
    """
    error_class = classify_exception(exc) if exc else (error_code or "Error")
    code = error_code or getattr(exc, "code", "") or error_class
    recovery = plan_recovery(
        str(code),
        segment_id=segment_id or segment_uuid,
        attempt=attempt,
        details={"message": message},
    )
    stack = ""
    if exc is not None:
        stack = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
    record = {
        "run_id": run_id,
        "segment_uuid": segment_uuid or segment_id,
        "segment_id": segment_id or segment_uuid,
        "stage": stage,
        "error_class": error_class,
        "error_code": code,
        "message": message,
        "stack_trace": stack,
        "recovery_strategy": recovery.to_dict(),
        "diagnostic_zip": diagnostic_zip,
    }
    if extra:
        record["extra"] = extra
    return record


def format_error_log_line(record: dict[str, Any]) -> str:
    return (
        f"[P16-ERR] run={record.get('run_id')} "
        f"seg={record.get('segment_uuid')} "
        f"stage={record.get('stage')} "
        f"class={record.get('error_class')} "
        f"recovery={((record.get('recovery_strategy') or {}).get('action'))} "
        f"zip={record.get('diagnostic_zip') or '-'} "
        f"msg={record.get('message')}"
    )
