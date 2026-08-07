# -*- coding: utf-8 -*-
"""Stage 23b — hard audio presence checks (no empty/tiny wav into mux)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.audio_presence")

MIN_AUDIO_BYTES = 1000


def audio_stat(path: str | Path | None) -> tuple[bool, int]:
    """Return (exists_and_usable, size_bytes). Usable ⇒ file and size ≥ MIN_AUDIO_BYTES."""
    if not path:
        return False, 0
    try:
        p = Path(str(path))
        if not p.is_file():
            return False, 0
        size = int(p.stat().st_size)
        return size >= MIN_AUDIO_BYTES, size
    except Exception:
        return False, 0


def resolve_segment_audio_path(
    seg: dict[str, Any],
    *,
    resolve_path: Callable[[str], str] | None = None,
) -> str:
    """Best-effort absolute/relative path from segment fields."""
    raw = str(seg.get("file") or seg.get("tts_file_path") or "").strip()
    if not raw:
        return ""
    if resolve_path:
        try:
            resolved = resolve_path(raw)
            if resolved:
                return str(resolved)
        except Exception:
            pass
    return raw


def stamp_audio_presence(
    seg: dict[str, Any],
    *,
    resolve_path: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Stamp audio_exists / audio_size_bytes on seg and stage23 meta."""
    path = resolve_segment_audio_path(seg, resolve_path=resolve_path)
    ok, size = audio_stat(path)
    seg["audio_exists"] = bool(ok)
    seg["audio_size_bytes"] = int(size)
    meta = dict(seg.get("stage23") or {})
    meta["audio_exists"] = bool(ok)
    meta["audio_size_bytes"] = int(size)
    seg["stage23"] = meta
    if not ok:
        # Clear dead path keys so repair / mux do not trust ghosts.
        if path and size == 0:
            logger.warning(
                "audio_presence: empty/missing file for seg=%s path=%s",
                seg.get("segment_id") or seg.get("index"),
                path,
            )
    return {"ok": ok, "size": size, "path": path}


def segment_needs_audio_repair(
    seg: dict[str, Any],
    *,
    resolve_path: Callable[[str], str] | None = None,
) -> bool:
    """True when active segment has speakable text but no usable audio."""
    if not isinstance(seg, dict):
        return False
    if seg.get("merged_into") is not None or seg.get("merged_into_id"):
        return False
    if seg.get("tts_blocked") or seg.get("skip_tts"):
        return False
    text = str(
        seg.get("final_tts_text")
        or seg.get("plain_text")
        or seg.get("text")
        or ""
    ).strip()
    if not text:
        return False
    path = resolve_segment_audio_path(seg, resolve_path=resolve_path)
    ok, _size = audio_stat(path)
    if not ok:
        return True
    try:
        tts_ms = int(
            seg.get("tts_ms")
            or seg.get("playback_duration")
            or seg.get("actual_duration_ms")
            or 0
        )
    except (TypeError, ValueError):
        tts_ms = 0
    if tts_ms <= 0 and bool(
        seg.get("needs_re_tts") or seg.get("split_child") or seg.get("pending_regen")
    ):
        return True
    if seg.get("status") in ("pending_regen",) or seg.get("tts_status") in (
        "pending_regen",
    ):
        return True
    return False
