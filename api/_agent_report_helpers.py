"""Shared helpers for director / semantic / grammar report APIs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

logger = logging.getLogger("tubedub.agent_report_helpers")

_APP_DIR = Path(__file__).resolve().parent.parent
_MANIFESTS_DIR = _APP_DIR / "output" / "manifests"


def safe_task_id(task_id: str) -> str | None:
    safe = Path(task_id).name
    if not safe or safe != task_id:
        return None
    return safe


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("load_json failed %s: %s", path, exc)
        return None


def task_info(task_id: str) -> dict[str, Any]:
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id) or {}
        return dict(task.get("info") or {})


def resolve_report_path(
    task_id: str,
    *,
    info_key: str,
    filename: str,
) -> Path | None:
    """Resolve agent report path from task info, project uuid, or disk scan."""
    from engines.path_safety import is_under_root

    allowed_roots = (
        _MANIFESTS_DIR,
        _APP_DIR / "output" / "sessions",
        _APP_DIR / "output",
    )

    def _accept(p: Path) -> Path | None:
        try:
            resolved = p.resolve()
        except OSError:
            return None
        if not resolved.is_file():
            return None
        for root in allowed_roots:
            try:
                if is_under_root(resolved, root.resolve()):
                    return resolved
            except OSError:
                continue
        return None

    info = task_info(task_id)
    report_path = info.get(info_key)
    if report_path:
        hit = _accept(Path(str(report_path)))
        if hit is not None:
            return hit

    project_uuid = info.get("project_uuid")
    if project_uuid:
        safe_uuid = Path(str(project_uuid)).name
        candidate = _MANIFESTS_DIR / safe_uuid / filename
        hit = _accept(candidate)
        if hit is not None:
            return hit

    # Fallback: scan manifests for matching task_id inside report JSON
    if _MANIFESTS_DIR.is_dir():
        for child in sorted(_MANIFESTS_DIR.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            candidate = child / filename
            data = load_json(candidate)
            if not data:
                continue
            if str(data.get("task_id") or "") == task_id:
                hit = _accept(candidate)
                if hit is not None:
                    return hit

    # Session folder fallback
    session = _APP_DIR / "output" / "sessions" / task_id / filename
    return _accept(session)


def resolve_manifest_dir(task_id: str) -> Path | None:
    info = task_info(task_id)
    report_path = info.get("director_report_path")
    if report_path:
        p = Path(str(report_path))
        if p.is_file():
            return p.parent
    manifest_path = info.get("manifest_path")
    if manifest_path:
        p = Path(str(manifest_path))
        if p.is_file():
            return p.parent
    project_uuid = info.get("project_uuid")
    if project_uuid:
        d = _MANIFESTS_DIR / str(project_uuid)
        if d.is_dir():
            return d
    # Scan for director_report with matching task_id
    path = resolve_report_path(
        task_id, info_key="director_report_path", filename="director_report.json"
    )
    return path.parent if path else None


def report_summary(report: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Extract a compact summary from agent report payloads."""
    segments = (
        report.get("per_segment")
        or report.get("segments")
        or report.get("results")
        or []
    )
    if not isinstance(segments, list):
        segments = []
    status = report.get("status") or report.get(f"{kind}_status") or "unknown"
    return {
        "kind": kind,
        "status": status,
        "project_uuid": report.get("project_uuid"),
        "task_id": report.get("task_id"),
        "segment_count": int(report.get("segment_count") or len(segments)),
        "warnings": list(report.get("warnings") or [])[:20],
        "errors": list(report.get("errors") or [])[:20],
        "execution_time_ms": report.get("execution_time_ms"),
        "version": report.get(f"{kind}_agent_version")
        or report.get("openddf_agent")
        or report.get("version"),
    }


def list_recent_reports(filename: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """List recent agent reports under output/manifests."""
    out: list[dict[str, Any]] = []
    if not _MANIFESTS_DIR.is_dir():
        return out
    for child in sorted(_MANIFESTS_DIR.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        path = child / filename
        data = load_json(path)
        if not data:
            continue
        out.append(
            {
                "project_uuid": data.get("project_uuid") or child.name,
                "task_id": data.get("task_id"),
                "status": data.get("status"),
                "segment_count": data.get("segment_count"),
                "path": str(path),
                "mtime": path.stat().st_mtime if path.is_file() else None,
            }
        )
        if len(out) >= limit:
            break
    return out


def segment_from_report(
    report: dict[str, Any],
    segment_id: int | str,
) -> dict[str, Any] | None:
    """Find a single segment brief/result inside a report."""
    key = str(segment_id)
    buckets = (
        report.get("per_segment"),
        report.get("segments"),
        report.get("results"),
        report.get("creative_briefs"),
    )
    for bucket in buckets:
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if not isinstance(item, dict):
                continue
            sid = item.get("segment_id")
            if sid is None:
                sid = item.get("index")
            if sid is None:
                sid = item.get("id")
            if str(sid) == key:
                return item
            # Also match list position when ids are 0-based integers
            try:
                if int(sid) == int(segment_id):
                    return item
            except (TypeError, ValueError):
                pass
    try:
        idx = int(segment_id)
    except (TypeError, ValueError):
        return None
    for bucket in buckets:
        if isinstance(bucket, list) and 0 <= idx < len(bucket):
            item = bucket[idx]
            return item if isinstance(item, dict) else {"value": item}
    return None
