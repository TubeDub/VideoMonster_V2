"""Audio identity — one segment ↔ one unique TTS file (No Audio Reuse).

Filenames are never used as segment identifiers. Every TTS artifact is bound to
``segment_id`` / ``segment_uuid`` and a run id. Collisions are repaired by
copying to a new unique path before Studio Handoff — never silently shared.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.audio_identity")


def ensure_segment_uuid(seg: dict[str, Any]) -> str:
    """Guarantee segment_uuid (== segment_id when present)."""
    from engines.pipeline_integrity.segment import new_segment_id

    sid = str(seg.get("segment_id") or "").strip()
    suid = str(seg.get("segment_uuid") or "").strip()
    if sid and suid and sid == suid:
        return sid
    if sid and not suid:
        seg["segment_uuid"] = sid
        return sid
    if suid and not sid:
        seg["segment_id"] = suid
        return suid
    if sid and suid and sid != suid:
        # Prefer segment_id as canonical; keep uuid alias in sync.
        seg["segment_uuid"] = sid
        return sid
    fresh = new_segment_id()
    seg["segment_id"] = fresh
    seg["segment_uuid"] = fresh
    return fresh


def ensure_all_segment_uuids(segments_data: list[dict[str, Any]]) -> None:
    from engines.pipeline_integrity.segment import ensure_segment_ids

    ensure_segment_ids(segments_data)
    for seg in segments_data:
        ensure_segment_uuid(seg)


def short_uuid(value: str, *, n: int = 12) -> str:
    raw = str(value or "").replace("-", "").strip()
    if not raw:
        raw = uuid.uuid4().hex
    return raw[:n]


def unique_tts_basename(
    *,
    segment_uuid: str,
    run_id: str = "",
    ext: str = ".wav",
    purpose: str = "tts",
) -> str:
    """Build a unique basename — never index-only names like seg0000.wav."""
    ext = ext if str(ext).startswith(".") else f".{ext}"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    nonce = uuid.uuid4().hex[:8]
    suid = short_uuid(segment_uuid, n=12)
    rid = short_uuid(run_id, n=8) if run_id else "run"
    purpose = "".join(c if c.isalnum() or c in "-_" else "_" for c in (purpose or "tts"))
    return f"{purpose}_{rid}_{suid}_{stamp}_{nonce}{ext}"


def allocate_tts_path(
    directory: Path,
    *,
    segment_uuid: str,
    run_id: str = "",
    ext: str = ".wav",
    purpose: str = "tts",
) -> Path:
    """Allocate a path that does not yet exist (atomic name reservation)."""
    directory.mkdir(parents=True, exist_ok=True)
    for _ in range(32):
        name = unique_tts_basename(
            segment_uuid=segment_uuid,
            run_id=run_id,
            ext=ext,
            purpose=purpose,
        )
        path = directory / name
        if not path.exists():
            return path
    # Extremely unlikely fallback
    return directory / f"{purpose}_{uuid.uuid4().hex}{ext}"


def write_bytes_atomic(path: Path, data: bytes) -> Path:
    """Write directly to the final unique path (no create-then-rename dance)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Never overwrite — allocate a sibling.
        path = allocate_tts_path(
            path.parent,
            segment_uuid=path.stem,
            ext=path.suffix or ".wav",
            purpose="tts",
        )
    path.write_bytes(data)
    return path


def copy_to_unique_path(
    src: Path,
    directory: Path,
    *,
    segment_uuid: str,
    run_id: str = "",
    purpose: str = "tts",
) -> Path:
    """Copy src into directory under a unique segment-bound name."""
    if not src.is_file():
        raise FileNotFoundError(str(src))
    ext = src.suffix or ".wav"
    dest = allocate_tts_path(
        directory,
        segment_uuid=segment_uuid,
        run_id=run_id,
        ext=ext,
        purpose=purpose,
    )
    shutil.copy2(src, dest)
    return dest


def bind_segment_audio(
    seg: dict[str, Any],
    path: str | Path,
    *,
    voice: str = "",
    duration_ms: int | None = None,
) -> str:
    """Bind a TTS file exclusively to one segment (updates file + tts_file_path)."""
    suid = ensure_segment_uuid(seg)
    name = Path(str(path)).name
    seg["file"] = name
    seg["tts_file_path"] = name
    seg["audio_bound_uuid"] = suid
    if voice:
        seg["tts_voice"] = voice
    if duration_ms is not None:
        seg["playback_duration"] = int(duration_ms)
        seg["tts_ms"] = int(duration_ms)
    return name


def _active_with_audio(segments_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

    out: list[dict[str, Any]] = []
    for seg in segments_data:
        if seg.get("merged_into") is not None or seg.get("merged_into_id"):
            continue
        if resolve_segment_audio_ref(seg):
            out.append(seg)
    return out


def find_duplicate_filenames(
    segments_data: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Map basename → list of segment_uuid that share it."""
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

    by_name: dict[str, list[str]] = {}
    for seg in _active_with_audio(segments_data):
        name = Path(str(resolve_segment_audio_ref(seg) or "")).name
        if not name:
            continue
        suid = ensure_segment_uuid(seg)
        by_name.setdefault(name, []).append(suid)
    return {k: v for k, v in by_name.items() if len(v) > 1}


def repair_duplicate_tts_filenames(
    segments_data: list[dict[str, Any]],
    *,
    resolve_path: Callable[[str], Path | str],
    dest_dir: Path,
    run_id: str = "",
) -> list[dict[str, Any]]:
    """
    If multiple active segments share a basename, copy the file to a new unique
    name for every conflicting owner after the first. Never share one WAV.
    """
    ensure_all_segment_uuids(segments_data)
    dupes = find_duplicate_filenames(segments_data)
    if not dupes:
        return []

    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

    repairs: list[dict[str, Any]] = []
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Build uuid → seg for quick lookup
    by_uuid = {ensure_segment_uuid(s): s for s in segments_data}

    for filename, owners in dupes.items():
        # Keep first owner on the original name; rebind the rest.
        for suid in owners[1:]:
            seg = by_uuid.get(suid)
            if not seg:
                continue
            ref = resolve_segment_audio_ref(seg) or filename
            try:
                src = Path(str(resolve_path(str(ref))))
            except Exception:
                src = Path(str(ref))
            if not src.is_file():
                # Try dest_dir / filename
                alt = dest_dir / Path(str(ref)).name
                src = alt if alt.is_file() else src
            if not src.is_file():
                logger.warning(
                    "[AudioIdentity] cannot repair seg=%s missing file=%s",
                    suid,
                    ref,
                )
                repairs.append(
                    {
                        "segment_uuid": suid,
                        "old_file": filename,
                        "new_file": None,
                        "status": "missing_source",
                    }
                )
                continue
            try:
                new_path = copy_to_unique_path(
                    src,
                    dest_dir,
                    segment_uuid=suid,
                    run_id=run_id,
                    purpose="tts_dedupe",
                )
            except Exception as exc:
                logger.warning("[AudioIdentity] copy failed seg=%s: %s", suid, exc)
                repairs.append(
                    {
                        "segment_uuid": suid,
                        "old_file": filename,
                        "new_file": None,
                        "status": f"copy_failed:{exc}",
                    }
                )
                continue
            duration = None
            try:
                from engines.pipeline_integrity.tts_segment_fields import (
                    measure_playback_duration_ms,
                )

                duration = measure_playback_duration_ms(new_path) or None
            except Exception:
                pass
            bind_segment_audio(seg, new_path, duration_ms=duration)
            repairs.append(
                {
                    "segment_uuid": suid,
                    "old_file": filename,
                    "new_file": new_path.name,
                    "status": "repaired",
                }
            )
            logger.info(
                "[AudioIdentity] deduped seg=%s %s → %s",
                suid,
                filename,
                new_path.name,
            )
    return repairs


def validate_audio_identity(
    segments_data: list[dict[str, Any]],
) -> dict[str, Any]:
    """Integrity check: 1 segment → 1 TTS, unique filenames & UUIDs."""
    ensure_all_segment_uuids(segments_data)
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

    active = _active_with_audio(segments_data)
    uuids = [ensure_segment_uuid(s) for s in active]
    names = [Path(str(resolve_segment_audio_ref(s) or "")).name for s in active]
    paths = [str(resolve_segment_audio_ref(s) or "") for s in active]

    dup_uuid = len(uuids) != len(set(uuids))
    dup_name = len(names) != len(set(names))
    dup_path = len(paths) != len(set(paths))
    empty = [u for u, n in zip(uuids, names) if not n]

    # Reverse map: file → owners
    file_owners: dict[str, list[str]] = {}
    for s in active:
        n = Path(str(resolve_segment_audio_ref(s) or "")).name
        file_owners.setdefault(n, []).append(ensure_segment_uuid(s))

    ok = not dup_uuid and not dup_name and not dup_path and not empty
    return {
        "ok": ok,
        "active_with_tts": len(active),
        "unique_uuids": len(set(uuids)),
        "unique_filenames": len(set(names)),
        "duplicate_uuids": dup_uuid,
        "duplicate_filenames": dup_name,
        "duplicate_paths": dup_path,
        "empty_audio_refs": empty,
        "shared_files": {k: v for k, v in file_owners.items() if len(v) > 1},
    }


def build_audio_registry(
    segments_data: list[dict[str, Any]],
    *,
    run_id: str = "",
    resolve_path: Callable[[str], Path | str] | None = None,
) -> dict[str, Any]:
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

    ensure_all_segment_uuids(segments_data)
    entries: list[dict[str, Any]] = []
    for seg in segments_data:
        if seg.get("merged_into") is not None or seg.get("merged_into_id"):
            continue
        ref = resolve_segment_audio_ref(seg)
        if not ref:
            continue
        suid = ensure_segment_uuid(seg)
        full = ""
        if resolve_path:
            try:
                full = str(resolve_path(str(ref)))
            except Exception:
                full = str(ref)
        else:
            full = str(ref)
        entries.append(
            {
                "segment_uuid": suid,
                "segment_id": suid,
                "index": seg.get("index"),
                "tts_file": Path(str(ref)).name,
                "tts_path": full,
                "voice": seg.get("tts_voice") or seg.get("voice") or "",
                "duration": int(
                    seg.get("playback_duration")
                    or seg.get("tts_ms")
                    or seg.get("actual_duration_ms")
                    or 0
                ),
                "run_id": run_id,
            }
        )
    return {
        "run_id": run_id,
        "segment_count": len(entries),
        "segments": entries,
    }


def write_audio_registry(
    registry: dict[str, Any],
    *,
    app_dir: Path,
    task_id: str,
) -> Path:
    out_dir = app_dir / "output" / "diagnostics" / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "audio_registry.json"
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_audio_identity_report(
    segments_data: list[dict[str, Any]],
    *,
    run_id: str = "",
    repairs: list[dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
    resolve_path: Callable[[str], Path | str] | None = None,
    handoff_ok: bool | None = None,
) -> dict[str, Any]:
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

    ensure_all_segment_uuids(segments_data)
    validation = validation or validate_audio_identity(segments_data)
    rows: list[dict[str, Any]] = []
    for seg in segments_data:
        if seg.get("merged_into") is not None or seg.get("merged_into_id"):
            continue
        ref = resolve_segment_audio_ref(seg)
        suid = ensure_segment_uuid(seg)
        full = ""
        if ref and resolve_path:
            try:
                full = str(resolve_path(str(ref)))
            except Exception:
                full = str(ref)
        rows.append(
            {
                "run_id": run_id,
                "segment_uuid": suid,
                "index": seg.get("index"),
                "tts_filename": Path(str(ref)).name if ref else None,
                "full_path": full or None,
                "voice_id": seg.get("tts_voice") or seg.get("voice") or "",
                "unique_ok": bool(ref)
                and Path(str(ref)).name
                not in (validation.get("shared_files") or {}),
                "status": "ok" if ref else "missing",
            }
        )
    return {
        "run_id": run_id,
        "handoff_ok": bool(validation.get("ok")) if handoff_ok is None else handoff_ok,
        "validation": validation,
        "repairs": repairs or [],
        "segments": rows,
    }


def write_audio_identity_report(
    report: dict[str, Any],
    *,
    app_dir: Path,
    task_id: str,
) -> Path:
    out_dir = app_dir / "output" / "diagnostics" / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "audio_identity_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ensure_unique_before_handoff(
    segments_data: list[dict[str, Any]],
    *,
    resolve_path: Callable[[str], Path | str],
    dest_dir: Path,
    run_id: str,
    app_dir: Path | None = None,
    hard_fail: bool | None = None,
) -> dict[str, Any]:
    """
    Studio Handoff preflight for audio identity.

    Default: repair duplicates (legacy). MASTER TZ v3.0 hard-fail when
    ``hard_fail=True`` or ``VM_AUDIO_IDENTITY_HARD_FAIL=1`` — raises
    PipelineAudioIdentityError instead of silent repair.
    """
    import os

    if hard_fail is None:
        hard_fail = str(
            os.environ.get("VM_AUDIO_IDENTITY_HARD_FAIL", "")
        ).strip().lower() in ("1", "true", "yes")

    ensure_all_segment_uuids(segments_data)
    repairs: list[Any] = []
    validation = validate_audio_identity(segments_data)
    if hard_fail and not validation.get("ok"):
        from engines.pipeline_integrity.exceptions import PipelineAudioIdentityError

        raise PipelineAudioIdentityError(
            "PIPELINE_AUDIO_IDENTITY: duplicate or invalid TTS bindings "
            "(hard-fail mode; omit hard_fail or unset VM_AUDIO_IDENTITY_HARD_FAIL for repair)",
            stage="handoff",
            details={"validation": validation},
        )
    if not hard_fail:
        repairs = repair_duplicate_tts_filenames(
            segments_data,
            resolve_path=resolve_path,
            dest_dir=dest_dir,
            run_id=run_id,
        )
        validation = validate_audio_identity(segments_data)
    registry = build_audio_registry(
        segments_data, run_id=run_id, resolve_path=resolve_path
    )
    report = build_audio_identity_report(
        segments_data,
        run_id=run_id,
        repairs=repairs,
        validation=validation,
        resolve_path=resolve_path,
        handoff_ok=bool(validation.get("ok")),
    )
    paths: dict[str, str] = {}
    if app_dir is not None:
        paths["audio_registry"] = str(
            write_audio_registry(registry, app_dir=app_dir, task_id=run_id)
        )
        paths["audio_identity_report"] = str(
            write_audio_identity_report(report, app_dir=app_dir, task_id=run_id)
        )
    return {
        "ok": bool(validation.get("ok")),
        "repairs": repairs,
        "validation": validation,
        "registry": registry,
        "report": report,
        "paths": paths,
        "hard_fail": hard_fail,
    }
