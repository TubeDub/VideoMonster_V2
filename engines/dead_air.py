# -*- coding: utf-8 -*-
"""Stage 17/18 — post-mux dead-air audit vs EN speech mask + hard-fail."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from pydub import AudioSegment
from pydub.silence import detect_silence

logger = logging.getLogger("tubedub.dead_air")

MAX_DEAD_AIR_MS = 350
# pydub silence: chunks quieter than this (dBFS) count as silence.
_SILENCE_THRESH_DBFS = -40
_MIN_SILENCE_LEN_MS = 350

PIPELINE_DEAD_AIR = "PIPELINE_DEAD_AIR"


def allow_dead_air_override() -> bool:
    """VM_ALLOW_DEAD_AIR=1 → warning only (debug). Default off on Simple."""
    return str(os.getenv("VM_ALLOW_DEAD_AIR") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def intervals_overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return int(a0) < int(b1) and int(a1) > int(b0)


def en_speech_mask_from_timing(
    timing_map: list[Any] | None,
) -> list[tuple[int, int]]:
    """Build EN speech intervals from Whisper/timing segment [start,end] ms."""
    out: list[tuple[int, int]] = []
    for item in timing_map or []:
        if isinstance(item, dict):
            s = int(item.get("start") or item.get("start_ms") or 0)
            e = int(item.get("end") or item.get("end_ms") or 0)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            s, e = int(item[0]), int(item[1])
        else:
            continue
        if e > s:
            out.append((s, e))
    return out


def silence_regions(
    audio_path: str | Path,
    *,
    min_silence_ms: int = _MIN_SILENCE_LEN_MS,
    silence_thresh_dbfs: float = _SILENCE_THRESH_DBFS,
) -> list[tuple[int, int]]:
    """Return [(start_ms, end_ms), ...] silent regions on the dub track."""
    path = Path(audio_path)
    if not path.is_file():
        return []
    audio = AudioSegment.from_file(str(path))
    return [
        (int(a), int(b))
        for a, b in detect_silence(
            audio,
            min_silence_len=int(min_silence_ms),
            silence_thresh=float(silence_thresh_dbfs),
        )
    ]


def find_dead_air_regions(
    dub_audio_path: str | Path,
    en_speech_intervals: list[tuple[int, int]],
    *,
    max_silence_ms: int = MAX_DEAD_AIR_MS,
    silence_thresh_dbfs: float = _SILENCE_THRESH_DBFS,
) -> list[dict[str, Any]]:
    """Flag silence > max_silence_ms that overlaps EN speech mask.

    Returns dead_air_regions[] with start_ms / end_ms / duration_ms.
    Empty when only EN-pause zones are silent (acceptance).
    """
    regions: list[dict[str, Any]] = []
    if not en_speech_intervals:
        return regions
    silences = silence_regions(
        dub_audio_path,
        min_silence_ms=int(max_silence_ms),
        silence_thresh_dbfs=silence_thresh_dbfs,
    )
    for s0, s1 in silences:
        dur = int(s1) - int(s0)
        if dur <= int(max_silence_ms):
            continue
        overlaps_en = any(
            intervals_overlap(s0, s1, e0, e1) for e0, e1 in en_speech_intervals
        )
        if not overlaps_en:
            continue
        regions.append(
            {
                "start_ms": int(s0),
                "end_ms": int(s1),
                "duration_ms": dur,
                "en_speech": True,
            }
        )
    if regions:
        logger.warning(
            "dead_air: %d region(s) >%dms on EN speech (first=%s)",
            len(regions),
            max_silence_ms,
            regions[0],
        )
    else:
        logger.info("dead_air: no EN-speech silence regions >%dms", max_silence_ms)
    return regions


def stamp_segment_dead_air_fields(
    segments_data: list,
    fitted_placements: list[dict],
    *,
    voice_id: str = "",
) -> None:
    """Write slot_ms / tts_ms / dead_air_ms / voice_id onto segments for Review/trace."""
    by_idx = {
        int(p.get("idx")): p
        for p in (fitted_placements or [])
        if isinstance(p, dict) and p.get("idx") is not None
    }
    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        place = by_idx.get(i) or {}
        if place:
            if place.get("slot_ms") is not None:
                seg["slot_ms"] = int(place["slot_ms"])
            if place.get("tts_ms") is not None:
                seg["tts_ms"] = int(place["tts_ms"])
            dead = place.get("dead_air_ms")
            if dead is None:
                slot = int(place.get("slot_ms") or 0)
                speech = int(place.get("speech_ms") or place.get("fitted_ms") or 0)
                dead = max(0, slot - speech)
            seg["dead_air_ms"] = int(dead)
            if place.get("dead_air_unresolved") or int(dead) > MAX_DEAD_AIR_MS:
                seg["dead_air_unresolved"] = True
        if voice_id:
            seg["voice_id"] = str(voice_id)
        elif seg.get("assigned_voice") or seg.get("voice"):
            seg["voice_id"] = str(seg.get("assigned_voice") or seg.get("voice") or "")


def append_dead_air_to_trace(
    app_dir: str | Path,
    *,
    task_id: str = "",
    regions: list[dict[str, Any]] | None = None,
    timing_rows: list[dict[str, Any]] | None = None,
    voice_id: str = "",
    phase: str = "dead_air",
) -> str:
    """Append Stage 17/18 dead-air block to output/dev/translation_trace.log."""
    from datetime import datetime, timezone

    log_dir = Path(app_dir) / "output" / "dev"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "translation_trace.log"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"=== task={task_id} phase={phase} ts={ts} ===",
        f"voice_id={voice_id or '-'}",
        f"dead_air_count={len(regions or [])}",
    ]
    for r in regions or []:
        lines.append(
            f"dead_air\tstart_ms={r.get('start_ms')}\tend_ms={r.get('end_ms')}\t"
            f"duration_ms={r.get('duration_ms')}\ten_speech={r.get('en_speech')}"
        )
    if not regions:
        lines.append("dead_air_regions=empty")
    for row in timing_rows or []:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"seg\tidx={row.get('idx')}\tslot_ms={row.get('slot_ms')}\t"
            f"tts_ms={row.get('tts_ms')}\tdead_air_ms={row.get('dead_air_ms')}\t"
            f"voice_id={row.get('voice_id') or voice_id or '-'}\t"
            f"tts_text_hash={row.get('tts_text_hash') or '-'}\t"
            f"strategy={row.get('strategy') or '-'}\t"
            f"dead_air_unresolved={bool(row.get('dead_air_unresolved'))}"
        )
    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")
    logger.info("dead_air: wrote %d region(s) to %s phase=%s", len(regions or []), log_path, phase)
    return str(log_path)


class DeadAirError(RuntimeError):
    """Raised when EN-speech zones still have silence > threshold after mux."""

    def __init__(
        self,
        message: str | list | None = None,
        *,
        regions: list | None = None,
    ):
        # Compat: DeadAirError(regions_list) or DeadAirError(msg, regions=...)
        if isinstance(message, list):
            regions = message
            message = None
        self.regions = list(regions or [])
        self.error_code = PIPELINE_DEAD_AIR
        msg = str(message) if message else (
            f"{PIPELINE_DEAD_AIR}: {len(self.regions)} silence region(s) "
            f">{MAX_DEAD_AIR_MS}ms on EN-speech zones"
        )
        super().__init__(msg)


def enforce_dead_air_or_fail(
    regions: list[dict] | None,
    *,
    simple_mode: bool = True,
    allow_override: bool | None = None,
) -> list:
    """Simple/Happy Path: non-empty dead_air on EN-speech → raise DeadAirError.

    Override: VM_ALLOW_DEAD_AIR=1 → log warning, return regions (no raise).
    Non-simple: never raise (return regions).
    """
    regs = list(regions or [])
    # Prefer EN-speech-flagged rows; plain dicts from tests also count.
    en_regs = [r for r in regs if isinstance(r, dict) and r.get("en_speech")]
    if en_regs:
        regs = en_regs
    if not simple_mode or not regs:
        return list(regions or [])
    override = (
        allow_dead_air_override() if allow_override is None else bool(allow_override)
    )
    if override:
        logger.warning(
            "dead_air: VM_ALLOW_DEAD_AIR=1 — warning only, count=%d",
            len(regs),
        )
        return regs
    raise DeadAirError(
        f"PIPELINE_DEAD_AIR: {len(regs)} silence region(s) "
        f">{MAX_DEAD_AIR_MS}ms on EN-speech zones",
        regions=regs,
    )


def audit_dead_air_post_mux(
    dub_audio_path: str | Path,
    timing_map: list[Any] | None,
    *,
    segments_data: list | None = None,
    fitted_placements: list[dict] | None = None,
    voice_id: str = "",
    task_info: dict | None = None,
    simple_mode: bool = False,
    hard_fail: bool = False,
) -> dict[str, Any]:
    """Full post-mux audit; optionally stamps task_info + hard-fails (Stage 18)."""
    en_mask = en_speech_mask_from_timing(timing_map)
    regions = find_dead_air_regions(dub_audio_path, en_mask)
    if segments_data is not None and fitted_placements is not None:
        stamp_segment_dead_air_fields(
            segments_data, fitted_placements, voice_id=voice_id
        )
    # Per-seg unresolved underfill also counts toward fail signal in logs.
    unresolved = []
    if segments_data:
        for i, seg in enumerate(segments_data):
            if isinstance(seg, dict) and seg.get("dead_air_unresolved"):
                unresolved.append(i)
    report = {
        "dead_air_regions": regions,
        "dead_air_count": len(regions),
        "en_speech_intervals": len(en_mask),
        "max_allowed_ms": MAX_DEAD_AIR_MS,
        "dead_air_unresolved_segs": unresolved,
    }
    if task_info is not None:
        task_info["dead_air_regions"] = regions
        task_info["dead_air_audit"] = {
            "count": len(regions),
            "max_allowed_ms": MAX_DEAD_AIR_MS,
            "en_speech_intervals": len(en_mask),
            "unresolved_segs": unresolved,
        }
    if hard_fail:
        enforce_dead_air_or_fail(regions, simple_mode=bool(simple_mode) or True)
    return report
