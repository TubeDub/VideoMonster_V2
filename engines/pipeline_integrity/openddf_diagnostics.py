"""
TubeDub Passive OpenDDF adapter.

Observes StageSnapshotGuard failures and enriches diagnostics only.
Does NOT change StageSnapshotGuard validation, Allowed Mutations, or pipeline data.
"""

from __future__ import annotations

import inspect
import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openddf import __version__ as OPENDDF_SDK_VERSION
from openddf.utils import filter_sensitive_data

from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError
from engines.pipeline_integrity.guards import STAGE_OWNER_MODULES, StageSnapshotGuard
from engines.pipeline_integrity.passive_openddf import (
    attach_passive_metadata,
    ensure_session,
    observe_stage_check,
    observe_stage_failure,
    observe_stage_success,
    resolve_output_dir,
)
from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage

logger = logging.getLogger("tubedub.openddf")

PIPELINE_STAGE_ORDER: list[tuple[str, str]] = [
    ("stt", "STT"),
    ("translate", "Translation"),
    ("timing_aware_translation", "Timing-Aware Translation"),
    ("tts", "TTS"),
    ("timing", "Timing"),
    ("slot_fit", "slot_fit"),
    ("studio_handoff", "Studio"),
]

_PROTECTED_FIELD_HINTS: frozenset[str] = frozenset(
    {
        "segment_id",
        "index",
        "plain_text",
        "translation_text",
        "text",
    }
)


def _repr_value(value: Any, *, limit: int = 500) -> str:
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


def _display_value(value: Any, *, limit: int = 500) -> str:
    """Human-readable value for Developer Mode panels (None → None)."""
    if value is None:
        return "None"
    if isinstance(value, str):
        text = value if value else '""'
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, default=str)
        return value
    except TypeError:
        return _repr_value(value)


def build_structured_diff(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for v in violations:
        diffs.append(
            {
                "segment_id": v.get("segment_id"),
                "field": v.get("field"),
                "old_value": _json_safe(v.get("old_value")),
                "new_value": _json_safe(v.get("new_value")),
                "structured_diff": {
                    "field": v.get("field"),
                    "old": _repr_value(v.get("old_value")),
                    "new": _repr_value(v.get("new_value")),
                },
            }
        )
    if not diffs and before and after:
        by_id_b = {str(s.get("segment_id")): s for s in before if s.get("segment_id")}
        by_id_a = {str(s.get("segment_id")): s for s in after if s.get("segment_id")}
        for sid in sorted(set(by_id_b) | set(by_id_a)):
            b = by_id_b.get(sid, {})
            a = by_id_a.get(sid, {})
            keys = set(b.keys()) | set(a.keys())
            for key in sorted(keys):
                if b.get(key) == a.get(key):
                    continue
                diffs.append(
                    {
                        "segment_id": sid,
                        "field": key,
                        "old_value": _json_safe(b.get(key)),
                        "new_value": _json_safe(a.get(key)),
                        "structured_diff": {
                            "field": key,
                            "old": _repr_value(b.get(key)),
                            "new": _repr_value(a.get(key)),
                        },
                    }
                )
    return diffs


def resolve_traceability(*, stage: str, mutator_module: str) -> dict[str, Any]:
    """Best-effort caller location from stack (outside guard/diagnostics frames)."""
    owner = mutator_module or STAGE_OWNER_MODULES.get(stage, "")
    module_tail = owner.split(".")[-1] if owner else stage.replace("_", "")

    skip_parts = (
        "openddf_diagnostics",
        "pipeline_integrity/guards",
        "exceptions.py",
        "site-packages",
    )
    for frame_info in inspect.stack()[2:]:
        full = frame_info.filename.replace("\\", "/")
        if any(x in full for x in skip_parts):
            continue
        if module_tail and module_tail not in full:
            continue
        return {
            "module": module_tail or stage,
            "function": f"{frame_info.function}()",
            "file_path": Path(frame_info.filename).name,
            "line_number": frame_info.lineno,
            "full_path": full,
        }

    fallback_module = module_tail or stage
    return {
        "module": fallback_module,
        "function": "(unknown)",
        "file_path": f"{fallback_module}.py",
        "line_number": 0,
        "full_path": owner or fallback_module,
    }


def build_mutation_policy_context(
    *,
    stage: str,
    field: str,
    allowed_mutations: list[str],
) -> dict[str, Any]:
    allowed = sorted(set(allowed_mutations or []) | set(allowed_fields_for_stage(stage)))
    forbidden = [field] if field else []
    if field in _PROTECTED_FIELD_HINTS:
        reason = "Поле является защищённым архитектурным контрактом."
    elif field and field not in allowed:
        reason = f"Поле «{field}» не входит в Allowed Mutations этапа «{stage}»."
    else:
        reason = "Изменение запрещено Mutation Policy текущего этапа."
    return {
        "allowed": allowed,
        "forbidden": forbidden,
        "violated_rule": f"mutation_of_{field}" if field else "unknown",
        "reason": reason,
    }


def build_dependency_pipeline(failed_stage: str) -> list[str]:
    labels: list[str] = ["Segment"]
    reached_failed = False
    for key, label in PIPELINE_STAGE_ORDER:
        if not reached_failed:
            labels.append(label)
        if key == failed_stage:
            labels.append("FAILED")
            reached_failed = True
    if not reached_failed:
        labels.append(failed_stage or "?")
        labels.append("FAILED")
    return labels


def build_event_timeline(
    *,
    stage: str,
    field: str,
    task_info: dict[str, Any] | None,
    max_events: int = 10,
) -> list[dict[str, str]]:
    now = datetime.now(timezone.utc)
    events: list[dict[str, str]] = []

    runtime = list((task_info or {}).get("runtime_diagnostics") or [])
    for row in runtime[-max_events:]:
        ts = row.get("timestamp") or ""
        st = row.get("stage") or "?"
        dur = row.get("duration_ms")
        suffix = f" ({dur}ms)" if dur is not None else ""
        events.append(
            {
                "timestamp": ts,
                "message": f"{st} completed{suffix}",
            }
        )

    ts_now = now.strftime("%H:%M:%S")
    events.append({"timestamp": ts_now, "message": f"{stage} validation started"})
    if field:
        events.append(
            {"timestamp": ts_now, "message": f"{field} modified (snapshot diff detected)"}
        )
    events.append({"timestamp": ts_now, "message": "Snapshot validation failed"})

    return events[-max_events:]


def build_recovery_hint(
    *,
    stage: str,
    field: str,
    trace: dict[str, Any],
) -> dict[str, Any]:
    module = trace.get("module") or stage
    text = (
        f"Либо прекратить изменение {field},\n"
        f"либо добавить поле в Allowed Mutations этапа {stage},\n"
        "если изменение является частью архитектуры."
    )
    return {
        "probable_cause": f"Поле «{field}» изменено модулем {module}.",
        "text": text,
        "options": [
            f"прекратить изменение {field}",
            (
                f"добавить «{field}» в Allowed Mutations этапа «{stage}», "
                "если изменение является частью архитектуры"
            ),
        ],
        "note": "Автоматическое исправление запрещено (Passive OpenDDF).",
    }


def persist_snapshot_artifacts(
    *,
    task_id: str,
    stage: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    diff_payload: dict[str, Any],
    developer_payload: dict[str, Any] | None = None,
    base_dir: Path | None = None,
    stacktrace: str = "",
    task_info: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Delegate artifact persistence to passive OpenDDF session (read-only writes)."""
    session = ensure_session(task_id, output_dir=base_dir, task_info=task_info)
    if session is None:
        root = base_dir or Path("output")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        out_dir = root / "diagnostics" / task_id / f"stage_snapshot_{stage}_{ts}"
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "snapshot_before": str(out_dir / "snapshot_before.json"),
            "snapshot_after": str(out_dir / "snapshot_after.json"),
            "snapshot_diff": str(out_dir / "snapshot_diff.json"),
            "diagnostics_dir": str(out_dir),
        }
        (out_dir / "snapshot_before.json").write_text(
            json.dumps(filter_sensitive_data(before), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (out_dir / "snapshot_after.json").write_text(
            json.dumps(filter_sensitive_data(after), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (out_dir / "snapshot_diff.json").write_text(
            json.dumps(filter_sensitive_data(diff_payload), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return paths

    attach_passive_metadata(developer_payload or {})
    artifact_paths = session.persist_segment_bundle(
        stage=stage,
        before=before,
        after=after,
        diff_payload=diff_payload,
        developer_payload=developer_payload or {},
        stacktrace=stacktrace,
    )
    if developer_payload is not None:
        developer_payload.setdefault("artifacts", {})
        developer_payload["artifacts"].update(artifact_paths)
    return artifact_paths


def build_developer_payload(
    exc: StageSnapshotIntegrityError,
    *,
    before: list[dict[str, Any]] | None,
    after: list[dict[str, Any]] | None,
    task_id: str = "",
    task_info: dict[str, Any] | None = None,
    artifact_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    violations = list((exc.details or {}).get("violations") or [])
    if not violations and exc.field:
        violations = [
            {
                "segment_id": exc.segment_id,
                "field": exc.field,
                "old_value": exc.old_value,
                "new_value": exc.new_value,
                "allowed_mutations": exc.allowed_mutations,
                "mutator_module": exc.mutator_module,
            }
        ]
    primary = violations[0] if violations else {}
    field = str(primary.get("field") or exc.field or "?")
    stage = exc.stage or str(primary.get("stage") or "?")
    allowed = list(primary.get("allowed_mutations") or exc.allowed_mutations or [])
    trace = resolve_traceability(stage=stage, mutator_module=str(exc.mutator_module or ""))

    snapshot_diff = build_structured_diff(
        list(before or []),
        list(after or []),
        violations,
    )
    policy = build_mutation_policy_context(stage=stage, field=field, allowed_mutations=allowed)

    old_display = _display_value(primary.get("old_value", exc.old_value))
    new_display = _display_value(primary.get("new_value", exc.new_value))
    hint = build_recovery_hint(stage=stage, field=field, trace=trace)

    payload = {
        "version": "TubeDub-Passive-1.0",
        "mode": "passive",
        "sdk_version": OPENDDF_SDK_VERSION,
        "error_code": "STAGE_SNAPSHOT_INTEGRITY",
        "stage": stage,
        "segment_id": exc.segment_id or primary.get("segment_id"),
        "snapshot_diff": {
            "field": field,
            "old_value": old_display,
            "new_value": new_display,
            "structured_diff": snapshot_diff,
        },
        "traceability": trace,
        "mutation_policy": policy,
        "dependency_pipeline": build_dependency_pipeline(stage),
        "event_timeline": build_event_timeline(
            stage=stage,
            field=field,
            task_info=task_info,
        ),
        "recovery_hint": hint,
        "developer_details": {
            "field": field,
            "old_value": old_display,
            "new_value": new_display,
            "module": trace.get("module"),
            "function": trace.get("function"),
            "file": trace.get("file_path"),
            "line": trace.get("line_number"),
            "allowed_mutations": policy.get("allowed") or [],
            "recovery_hint": hint.get("text") or "",
        },
        "artifacts": artifact_paths or {},
        "violations_count": len(violations),
    }
    return attach_passive_metadata(payload)


def build_release_payload(exc: StageSnapshotIntegrityError) -> dict[str, Any]:
    stage = exc.stage or "?"
    return {
        "title": "Ошибка дубляжа",
        "error_code": "STAGE_SNAPSHOT_INTEGRITY",
        "error_type": "StageSnapshotIntegrityError",
        "stage": stage,
        "reason_short": (
            f"Не удалось завершить этап «{stage}». "
            "Данные сегмента изменены с нарушением правил обработки."
        ),
        "reason": exc.format_user_reason(),
    }


def format_developer_block(payload: dict[str, Any]) -> str:
    """Developer Mode «Подробнее» — factual diagnostic layout."""
    details = payload.get("developer_details") or {}
    diff = payload.get("snapshot_diff") or {}
    trace = payload.get("traceability") or {}
    policy = payload.get("mutation_policy") or {}
    hint = payload.get("recovery_hint") or {}

    field = details.get("field") or diff.get("field") or "?"
    old_val = details.get("old_value") or diff.get("old_value") or "?"
    new_val = details.get("new_value") or diff.get("new_value") or "?"
    module = details.get("module") or trace.get("module") or "?"
    function = details.get("function") or trace.get("function") or "?"
    file_name = details.get("file") or trace.get("file_path") or "?"
    line_no = details.get("line") if details.get("line") is not None else trace.get("line_number", "?")
    allowed = details.get("allowed_mutations") or policy.get("allowed") or []
    recovery = details.get("recovery_hint") or hint.get("text") or ""

    lines = [
        f"Field:\n{field}",
        f"Old Value:\n{old_val}",
        f"New Value:\n{new_val}",
        "",
        f"Поле:\n{field}",
        f"Изменилось:\n{old_val}\n↓\n{new_val}",
        "",
        f"Module:\n{module}",
        f"Function:\n{function}",
        f"File:\n{file_name}",
        f"Line:\n{line_no}",
        "",
        f"Источник:\n{file_name}",
        f"Функция:\n{function}",
        f"Строка:\n{line_no}",
        "",
        "Allowed Mutations:",
        *allowed,
        "",
        f"Recovery Hint:\n{recovery}",
    ]

    arts = payload.get("artifacts") or {}
    art_lines = [
        k
        for k in (
            "snapshot_before",
            "snapshot_after",
            "snapshot_diff",
            "report",
            "pipeline_log",
            "diagnostics_dir",
        )
        if arts.get(k)
    ]
    if art_lines:
        lines.extend(["", "— Diagnostics folder —"])
        for key in art_lines:
            lines.append(f"{key}: {arts.get(key)}")
    return "\n".join(lines)


def enrich_stage_snapshot_error(
    exc: StageSnapshotIntegrityError,
    *,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    task_id: str = "",
    task_info: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> StageSnapshotIntegrityError:
    """Attach OpenDDF v1.3 payload and persist snapshot artifacts."""
    violations = list((exc.details or {}).get("violations") or [])
    diff_payload = {
        "stage": exc.stage,
        "task_id": task_id,
        "segment_id": exc.segment_id,
        "violations": violations,
        "structured_diff": build_structured_diff(before, after, violations),
        "traceback": traceback.format_exc(),
    }

    session_dir = (task_info or {}).get("session_dir")
    base = resolve_output_dir(task_id=task_id, task_info=task_info, output_dir=output_dir)
    session = ensure_session(task_id, output_dir=base, task_info=task_info)

    developer = build_developer_payload(
        exc,
        before=before,
        after=after,
        task_id=task_id,
        task_info=task_info,
    )

    artifact_paths = persist_snapshot_artifacts(
        task_id=task_id or "unknown",
        stage=exc.stage or "unknown",
        before=before,
        after=after,
        diff_payload=diff_payload,
        developer_payload=developer,
        base_dir=base,
        stacktrace=traceback.format_exc(),
        task_info=task_info,
    )

    developer["artifacts"] = artifact_paths
    if developer.get("developer_details") is not None:
        developer["developer_details"]["artifacts_dir"] = artifact_paths.get("diagnostics_dir")

    release = build_release_payload(exc)

    exc.details["openddf"] = {
        "mode": "passive",
        "sdk_version": OPENDDF_SDK_VERSION,
        "release": release,
        "developer": developer,
        "developer_block": format_developer_block(developer),
        "artifacts": artifact_paths,
        "timeline_events": (session.timeline.get_events() if session else []),
    }
    observe_stage_failure(
        task_id,
        exc.stage or "unknown",
        error_type="StageSnapshotIntegrityError",
        field=exc.field or "",
    )
    logger.error(
        "[Passive-OpenDDF] stage=%s field=%s segment=%s mode=passive artifacts=%s",
        exc.stage,
        exc.field,
        exc.segment_id,
        artifact_paths.get("diagnostics_dir"),
    )
    return exc


def guard_check_with_diagnostics(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    stage: str,
    mutator_module: str | None = None,
    task_id: str = "",
    task_info: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> None:
    """
    Passive diagnostics wrapper: StageSnapshotGuard.check unchanged; enrich on failure.
    """
    base = resolve_output_dir(task_id=task_id, task_info=task_info, output_dir=output_dir)
    observe_stage_check(task_id, stage, task_info=task_info, output_dir=base)
    try:
        StageSnapshotGuard.check(
            before,
            after,
            stage=stage,
            mutator_module=mutator_module,
        )
        observe_stage_success(task_id, stage)
    except StageSnapshotIntegrityError as exc:
        enrich_stage_snapshot_error(
            exc,
            before=before,
            after=after,
            task_id=task_id,
            task_info=task_info,
            output_dir=base,
        )
        raise


def release_summary_from_exc(exc: StageSnapshotIntegrityError) -> dict[str, Any]:
    openddf = (exc.details or {}).get("openddf") or {}
    if openddf.get("release"):
        return dict(openddf["release"])
    return build_release_payload(exc)


def developer_payload_from_exc(exc: StageSnapshotIntegrityError) -> dict[str, Any] | None:
    openddf = (exc.details or {}).get("openddf") or {}
    return openddf.get("developer")


def developer_block_from_exc(exc: StageSnapshotIntegrityError) -> str:
    openddf = (exc.details or {}).get("openddf") or {}
    if openddf.get("developer_block"):
        return str(openddf["developer_block"])
    payload = openddf.get("developer")
    if payload:
        return format_developer_block(payload)
    return exc.format_diagnostic_block()
