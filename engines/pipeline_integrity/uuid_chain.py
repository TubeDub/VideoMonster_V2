"""UUID identity chain — Freeze TZ P3.

Every segment carries:
  segment_uuid, translation_uuid, tts_uuid, audio_uuid, merge_uuid

Filenames are never used as identifiers.
"""

from __future__ import annotations

import uuid
from typing import Any

UUID_FIELDS: tuple[str, ...] = (
    "segment_uuid",
    "source_segment_uuid",
    "translation_uuid",
    "adaptation_uuid",
    "tts_uuid",
    "audio_uuid",
    "merge_uuid",
)


def _new_uuid() -> str:
    return uuid.uuid4().hex


def ensure_segment_uuid(seg: dict[str, Any]) -> str:
    from engines.pipeline_integrity.audio_identity import ensure_segment_uuid as _ensure

    return _ensure(seg)


def ensure_translation_uuid(seg: dict[str, Any]) -> str:
    existing = str(seg.get("translation_uuid") or "").strip()
    if existing:
        return existing
    # Stable from segment + locked text when possible
    base = str(seg.get("segment_uuid") or seg.get("segment_id") or _new_uuid())
    text = str(
        seg.get("translated_text")
        or seg.get("translation_text")
        or seg.get("plain_text")
        or seg.get("text")
        or ""
    )
    if text:
        # Deterministic UUID-like hex from segment+text (not RFC UUID, but unique & stable)
        import hashlib

        digest = hashlib.sha256(f"{base}|{text}".encode("utf-8")).hexdigest()
        seg["translation_uuid"] = digest[:32]
        return seg["translation_uuid"]
    fresh = _new_uuid()
    seg["translation_uuid"] = fresh
    return fresh


def ensure_adaptation_uuid(seg: dict[str, Any], *, force_new: bool = False) -> str:
    try:
        from engines.pipeline_integrity.revision_manager import (
            ensure_adaptation_uuid as _ensure_ad,
        )

        return _ensure_ad(seg, force_new=force_new)
    except Exception:
        if not force_new:
            existing = str(seg.get("adaptation_uuid") or "").strip()
            if existing:
                return existing
        fresh = _new_uuid()
        seg["adaptation_uuid"] = fresh
        return fresh


def ensure_tts_uuid(seg: dict[str, Any], *, force_new: bool = False) -> str:
    if not force_new:
        existing = str(seg.get("tts_uuid") or "").strip()
        if existing:
            return existing
    fresh = _new_uuid()
    seg["tts_uuid"] = fresh
    return fresh


def ensure_audio_uuid(seg: dict[str, Any], *, force_new: bool = False) -> str:
    if not force_new:
        existing = str(seg.get("audio_uuid") or "").strip()
        if existing:
            return existing
    fresh = _new_uuid()
    seg["audio_uuid"] = fresh
    return fresh


def ensure_merge_uuid(seg: dict[str, Any], *, force_new: bool = False) -> str:
    if not force_new:
        existing = str(seg.get("merge_uuid") or "").strip()
        if existing:
            return existing
    fresh = _new_uuid()
    seg["merge_uuid"] = fresh
    return fresh


def ensure_all_uuids(seg: dict[str, Any]) -> dict[str, str]:
    """Stamp the full UUID chain on a segment."""
    try:
        from engines.pipeline_integrity.revision_manager import (
            ensure_source_segment_uuid,
        )

        source_uuid = ensure_source_segment_uuid(seg)
    except Exception:
        source_uuid = str(seg.get("source_segment_uuid") or seg.get("segment_id") or "")
        if source_uuid:
            seg["source_segment_uuid"] = source_uuid
    return {
        "segment_uuid": ensure_segment_uuid(seg),
        "source_segment_uuid": source_uuid or str(seg.get("segment_id") or ""),
        "translation_uuid": ensure_translation_uuid(seg),
        "adaptation_uuid": ensure_adaptation_uuid(seg),
        "tts_uuid": ensure_tts_uuid(seg),
        "audio_uuid": ensure_audio_uuid(seg),
        "merge_uuid": ensure_merge_uuid(seg),
    }


def ensure_project_uuids(segments: list[dict[str, Any]]) -> dict[str, Any]:
    seen: dict[str, set[str]] = {k: set() for k in UUID_FIELDS}
    duplicates: list[dict[str, str]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        ids = ensure_all_uuids(seg)
        for field, value in ids.items():
            if value in seen[field]:
                duplicates.append({"field": field, "value": value})
                # Repair duplicate by regenerating non-segment uuid
                if field == "segment_uuid":
                    continue
                if field == "translation_uuid":
                    seg.pop("translation_uuid", None)
                    ids[field] = ensure_translation_uuid(seg)
                elif field == "adaptation_uuid":
                    ids[field] = ensure_adaptation_uuid(seg, force_new=True)
                elif field == "tts_uuid":
                    ids[field] = ensure_tts_uuid(seg, force_new=True)
                elif field == "audio_uuid":
                    ids[field] = ensure_audio_uuid(seg, force_new=True)
                elif field == "merge_uuid":
                    ids[field] = ensure_merge_uuid(seg, force_new=True)
            seen[field].add(ids[field])
    return {
        "segments": len(segments),
        "unique": {k: len(v) for k, v in seen.items()},
        "duplicates_repaired": len(duplicates),
    }


def assert_uuids_unique(segments: list[dict[str, Any]]) -> None:
    from engines.pipeline_integrity.exceptions import PipelineIdentityError

    for field in UUID_FIELDS:
        values = [
            str(s.get(field) or "")
            for s in segments
            if isinstance(s, dict) and s.get(field)
        ]
        if len(values) != len(set(values)):
            raise PipelineIdentityError(
                f"duplicate {field} detected",
                stage="uuid_chain",
                details={"field": field, "count": len(values), "unique": len(set(values))},
            )
