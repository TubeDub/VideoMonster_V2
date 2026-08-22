# -*- coding: utf-8 -*-
"""Stage 23b — hard audio presence checks (no empty/tiny wav into mux)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.audio_presence")

MIN_AUDIO_BYTES = 1000

# Prefer live slot-fit / file over a stale group stamp (diag 2286c82f: ghost
# ``resolved_path=…_g0000.mp3`` hid an existing pause_run / tts_regen).
SEGMENT_AUDIO_KEYS = (
    "oss_segs_path",
    "fitted_file",
    "file",
    "tts_file_path",
    "resolved_path",
)


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


def iter_segment_audio_candidates(seg: dict[str, Any]) -> list[str]:
    """Unique non-empty path strings; fitted/file before ghost resolved_path."""
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(seg, dict):
        return out
    for key in SEGMENT_AUDIO_KEYS:
        val = str(seg.get(key) or "").strip()
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def resolve_segment_audio_path(
    seg: dict[str, Any],
    *,
    resolve_path: Callable[[str], str] | None = None,
) -> str:
    """First *existing* candidate among fitted_file / file / tts_file_path / resolved_path.

    Stage 32 (diag 2286c82f): a ghost ``resolved_path`` (group mp3, relative
    ``tts_*.mp3``) must not hide a live pause_run / slot_fit / tts_regen.
    If nothing exists, return the first raw candidate so LAST-RESORT can pad.
    """
    raws = iter_segment_audio_candidates(seg)
    if not raws:
        return ""

    def _try(raw: str) -> str:
        if resolve_path:
            try:
                resolved = resolve_path(raw)
                if resolved:
                    ok, _size = audio_stat(resolved)
                    if ok:
                        return str(resolved)
            except Exception:
                pass
        ok, _size = audio_stat(raw)
        if ok:
            return str(raw)
        return ""

    for raw in raws:
        hit = _try(raw)
        if hit:
            return hit
    return raws[0]


def assert_audio_file(path: str | Path, min_bytes: int = MIN_AUDIO_BYTES) -> Path:
    """Raise FileNotFoundError when audio is missing or smaller than min_bytes."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < int(min_bytes):
        raise FileNotFoundError(f"Audio missing or empty: {p}")
    return p


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
    """True when speakable segment has no usable audio / must re-TTS.

    TZ: text AND (no file OR size < 1000 OR tts_ms == 0 OR needs_re_tts
    OR force/post_restore split child) → repair before mux.
    """
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
    if bool(seg.get("needs_re_tts")):
        return True
    if seg.get("status") in ("pending_regen",) or seg.get("tts_status") in (
        "pending_regen",
        "failed",
    ):
        return True
    path = resolve_segment_audio_path(seg, resolve_path=resolve_path)
    ok, _size = audio_stat(path)
    if not ok:
        return True
    try:
        tts_ms = int(
            seg.get("tts_ms")
            or seg.get("playback_duration")
            or seg.get("actual_duration_ms")
            or seg.get("final_tts_duration_ms")
            or 0
        )
    except (TypeError, ValueError):
        tts_ms = 0
    if tts_ms <= 0:
        return True
    # Split children must never reach mux with inherited/empty audio.
    if bool(
        seg.get("force_split_executed")
        or seg.get("post_restore_split")
        or seg.get("split_child")
        or seg.get("needs_post_restore_split")
    ) and (not ok or tts_ms <= 0):
        return True
    return False
