"""Canonical TTS-stage segment fields (read-only plain_text after Translation)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TTS_ALLOWED_MUTATIONS: frozenset[str] = frozenset(
    {"tts_text", "tts_file_path", "playback_duration", "status"}
)


def resolve_segment_text_for_tts(seg: dict[str, Any]) -> str:
    """Priority: grammar_text > timing_text > semantic_text > translated_text > text."""
    return str(
        seg.get("grammar_text")
        or seg.get("timing_text")
        or seg.get("semantic_text")
        or seg.get("translated_text")
        or seg.get("text")
        or ""
    ).strip()


def resolve_tts_input_text(group: dict[str, Any]) -> str:
    """
    Text sent to the TTS engine for a group.
    Pronunciation / SSML / pauses live in group plain_text or SSML text — not segment.plain_text.
    """
    plain = str(group.get("plain_text") or "").strip()
    ssml_or_plain = str(group.get("text") or "").strip()
    text = plain if plain else ssml_or_plain
    if text.lstrip().startswith("<speak"):
        text = re.sub(r"<[^>]+>", " ", text).strip()
    return text


def apply_tts_synthesis_result(
    seg: dict[str, Any],
    *,
    tts_text: str,
    tts_file_path: str | None,
    playback_duration: int | None = None,
    status: str = "generated",
) -> None:
    """Mutate only TTS-contract fields on a segment row."""
    seg["tts_text"] = tts_text
    seg["tts_file_path"] = tts_file_path
    if playback_duration is not None:
        seg["playback_duration"] = int(playback_duration)
    seg["status"] = status


def apply_tts_group_merge_links(
    segments_data: list[dict[str, Any]],
    tts_groups: list[dict[str, Any]],
) -> None:
    """Establish merge pointers before TTS snapshot (not a TTS-stage mutation)."""
    for group in tts_groups:
        indices = group.get("indices") or []
        if len(indices) <= 1:
            continue
        head = int(indices[0])
        if head >= len(segments_data):
            continue
        head_sid = segments_data[head].get("segment_id")
        for rest_idx in indices[1:]:
            ri = int(rest_idx)
            if ri >= len(segments_data):
                continue
            segments_data[ri]["merged_into"] = head
            if head_sid:
                segments_data[ri]["merged_into_id"] = head_sid


def mark_merged_tts_children(
    segments_data: list[dict[str, Any]],
    indices: list[int],
) -> None:
    """Mark non-head group members after head synthesis (TTS-contract fields only)."""
    if len(indices) <= 1:
        return
    for rest_idx in indices[1:]:
        ri = int(rest_idx)
        if ri >= len(segments_data):
            continue
        apply_tts_synthesis_result(
            segments_data[ri],
            tts_text="",
            tts_file_path=None,
            status="merged",
        )


def sync_tts_legacy_fields(segments_data: list[dict[str, Any]]) -> None:
    """Map canonical TTS fields to legacy keys for slot_fit / studio (after TTS guard)."""
    for seg in segments_data:
        tfp = seg.get("tts_file_path")
        if tfp:
            name = Path(str(tfp)).name
            seg["file"] = name
        elif seg.get("status") in ("merged", "empty", "failed"):
            seg["file"] = None
        pd = seg.get("playback_duration")
        if pd is not None:
            seg["tts_ms"] = int(pd)
        st = seg.get("status")
        if st:
            seg["tts_status"] = st


def measure_playback_duration_ms(audio_path: str | Path | None) -> int:
    if not audio_path:
        return 0
    try:
        from pydub import AudioSegment

        return len(AudioSegment.from_file(str(audio_path)))
    except Exception:
        return 0


def resolve_segment_audio_ref(seg: dict[str, Any]) -> str | None:
    """
    Effective segment audio filename for integrity checks and mux.

    Prefer seg['file'] — slot_fit and studio stages update the working copy here.
    Fall back to tts_file_path when file is unset (merged/empty rows).
    """
    legacy = seg.get("file")
    if legacy:
        return Path(str(legacy)).name
    tfp = seg.get("tts_file_path")
    if tfp:
        return Path(str(tfp)).name
    return None
