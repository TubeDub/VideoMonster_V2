"""
TTS handoff diagnostics — HotFix №1.

Logs TTS file list at pipeline handoff points without changing dubbing algorithms.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.tts_handoff")


def _basename_list(paths: list[Any]) -> list[str]:
    out: list[str] = []
    for item in paths:
        if not item:
            continue
        out.append(Path(str(item)).name)
    return out


def log_tts_generated(
    task_id: str,
    *,
    tts_files: list[str],
    segments_data: list[dict[str, Any]] | None = None,
    artifacts_dir: Path | str | None = None,
) -> None:
    """Step 1 — after TTS generation completes."""
    seg_files = []
    if segments_data:
        for seg in segments_data:
            if seg.get("merged_into") is not None:
                continue
            name = seg.get("file")
            if name:
                seg_files.append(str(name))

    names = _basename_list(tts_files or seg_files)
    art = str(artifacts_dir) if artifacts_dir else "(unknown)"
    lines = [
        f"[TTS] task={task_id}",
        f"[TTS] Generated files: {len(names)}",
        f"[TTS] Storage directory: {art}",
        f"[TTS] tts_files list size: {len(tts_files or [])}",
        f"[TTS] segments_data with file: {len(seg_files)}",
    ]
    for name in names[:50]:
        lines.append(f"[TTS]   {name}")
    if len(names) > 50:
        lines.append(f"[TTS]   ... +{len(names) - 50} more")
    logger.info("\n".join(lines))


def log_pipeline_handoff(
    task_id: str,
    *,
    project_session: Any | None = None,
    task_info: dict[str, Any] | None = None,
    segments_data: list[dict[str, Any]] | None = None,
) -> None:
    """Step 2 — before next pipeline stage (studio_ready / slot_fit done)."""
    info = dict(task_info or {})
    session_keys: list[str] = []
    pipeline_state: dict[str, Any] = {}
    if project_session is not None:
        session_keys = sorted(getattr(project_session, "_data", {}) or {})
        try:
            pipeline_state = {
                "segments_count": len(project_session.get("segments") or []),
                "timing_map_count": len(project_session.get("timing_map") or []),
                "session_dir": str(project_session.session_dir),
            }
        except Exception as exc:
            pipeline_state = {"error": str(exc)}

    seg_paths = []
    if segments_data:
        for seg in segments_data:
            if seg.get("merged_into") is not None:
                continue
            if seg.get("file"):
                seg_paths.append(str(seg["file"]))

    lines = [
        f"[Handoff] task={task_id}",
        f"[Handoff] ProjectSession keys: {session_keys or '(none)'}",
        f"[Handoff] pipeline_state: {pipeline_state}",
        f"[Handoff] task_info.session_dir: {info.get('session_dir')!r}",
        f"[Handoff] task_info.tts_files count: {len(info.get('tts_files') or [])}",
        f"[Handoff] segment_paths (from segments_data): {len(seg_paths)}",
    ]
    for name in _basename_list(seg_paths)[:20]:
        lines.append(f"[Handoff]   {name}")
    logger.info("\n".join(lines))


def log_track_builder_input(
    task_id: str,
    *,
    segment_paths: list[str],
    segments_data: list[dict[str, Any]] | None = None,
    source: str = "track_builder",
) -> None:
    """Step 3 — immediately before timed track assembly."""
    lines = [
        f"[Track Builder] task={task_id} source={source}",
        f"[Track Builder] segment_paths count: {len(segment_paths)}",
    ]
    for path in segment_paths[:30]:
        lines.append(f"[Track Builder]   {path}")
    if len(segment_paths) > 30:
        lines.append(f"[Track Builder]   ... +{len(segment_paths) - 30} more")

    if segments_data:
        with_file = sum(
            1
            for s in segments_data
            if s.get("merged_into") is None and s.get("file")
        )
        lines.append(f"[Track Builder] segments_data rows with file: {with_file}/{len(segments_data)}")

    logger.info("\n".join(lines))


def log_empty_tts_diagnosis(
    task_id: str,
    *,
    task_info: dict[str, Any] | None = None,
    segments_data: list[dict[str, Any]] | None = None,
    segment_paths: list[str] | None = None,
    stage: str = "unknown",
) -> dict[str, Any]:
    """Step 4 — empty list: filesystem probe + state transfer stack.

    Returns a structured report (TZ §8) so the UI can show WHY the track has no
    TTS files instead of a bare error message.
    """
    info = dict(task_info or {})
    session_dir = info.get("session_dir")
    checks: list[str] = []
    fs_probe: list[dict[str, Any]] = []
    total_on_disk = 0

    search_paths = [
        ("session_dir", session_dir),
        ("output/sessions", Path("output") / "sessions" / task_id if task_id else None),
        ("temp/dubbing", Path("temp") / f"dubbing_{task_id}" if task_id else None),
    ]
    for label, directory in search_paths:
        if not directory:
            continue
        p = Path(str(directory))
        if p.is_dir():
            mp3s = sorted(p.glob("*.mp3"))
            total_on_disk += len(mp3s)
            checks.append(f"{label}={p} mp3_on_disk={len(mp3s)}")
            for mp3 in mp3s[:10]:
                checks.append(f"  disk: {mp3.name}")
            fs_probe.append(
                {
                    "label": label,
                    "path": str(p),
                    "exists": True,
                    "mp3_on_disk": len(mp3s),
                    "files": [m.name for m in mp3s[:20]],
                }
            )
        else:
            checks.append(f"{label}={p} missing")
            fs_probe.append(
                {"label": label, "path": str(p), "exists": False, "mp3_on_disk": 0}
            )

    seg_rows = [s for s in (segments_data or []) if s.get("merged_into") is None]
    translated = sum(1 for s in seg_rows if str(s.get("text") or "").strip())
    rows_with_file = sum(1 for s in seg_rows if s.get("file"))
    missing_segments = [
        {"index": s.get("index", i), "file": s.get("file"), "text_preview": str(s.get("text") or "")[:80]}
        for i, s in enumerate(seg_rows)
        if not s.get("file")
    ]
    tts_in_info = info.get("tts_files") or []
    expected = len(tts_in_info) or len(seg_rows)

    # Root-cause inference for the human-readable reason.
    if total_on_disk == 0 and expected > 0:
        reason = (
            "TTS audio files were generated but are no longer on disk — they were "
            "deleted before track assembly (premature cleanup of the session dir)."
        )
    elif total_on_disk > 0 and rows_with_file == 0:
        reason = (
            "TTS audio exists on disk but segment rows have no resolvable 'file' — "
            "path resolution / handoff lost the references."
        )
    elif expected == 0:
        reason = "No segments were sent to TTS (translation produced no usable segments)."
    else:
        reason = "TTS files missing for track assembly; see filesystem probe."

    report = {
        "stage": stage,
        "task_id": task_id,
        "segments_translated": translated,
        "segments_sent_to_tts": expected,
        "audio_files_on_disk": total_on_disk,
        "segments_with_file": rows_with_file,
        "missing_segments": missing_segments,
        "search_paths": [str(p) for _, p in search_paths if p],
        "filesystem_probe": fs_probe,
        "reason": reason,
    }

    stack = "".join(traceback.format_stack(limit=12))
    lines = [
        f"[TTS EMPTY] task={task_id} stage={stage}",
        f"[TTS EMPTY] reason: {reason}",
        f"[TTS EMPTY] translated={translated} sent_to_tts={expected} "
        f"on_disk={total_on_disk} rows_with_file={rows_with_file}",
        f"[TTS EMPTY] segment_paths count: {len(segment_paths or [])}",
        f"[TTS EMPTY] task_info.tts_files: {len(tts_in_info)}",
        f"[TTS EMPTY] filesystem:",
        *checks,
        f"[TTS EMPTY] state transfer stack:",
        stack,
    ]
    logger.error("\n".join(lines))
    return report
