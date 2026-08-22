# -*- coding: utf-8 -*-
"""OSS production dubbing patterns wired into VideoMonster Simple.

Stolen from (not a rewrite of this pipeline):
  VideoLingo   — output/audio/segs workdir, skip missing files, concat+silence
  pyVideoTrans — TEMP/{uuid} workdir, continue if any TTS succeeds, pad to video
  Softcatala open-dubbing — one assigned_voice per clip, speed after TTS
  SoniTranslate — avoid_overlap: shift next start instead of mix overlay

Stage 28–35 already cover UK lock, text-fit first, RU skip+pad, pad-before-census.
This module adds the remaining production gaps: one segs root, skip-missing mix,
sequential place, lock voice after first successful synth, mild 0.9–1.1 speed band.
"""

from __future__ import annotations

import logging
import re
import shutil
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.oss_production")

OSS_SEGS_SUBDIR = "segs"
OSS_SPEED_MIN = 0.90
OSS_SPEED_MAX = 1.10
OSS_MIN_GAP_MS = 80
OSS_MIN_AUDIO_BYTES = 1000
OSS_TEXT_DELTA_MS = 120
OSS_SPEED_DELTA_MS = 150

_GHOST_GROUP_RE = re.compile(r"_g\d{4}\.(mp3|wav)$", re.I)

_PATH_KEYS = ("fitted_file", "file", "tts_file_path", "resolved_path")


def resolve_oss_segs_dir(session_dir: str | Path | None) -> Path:
    """VideoLingo ``output/audio/segs`` — one absolute session segs folder."""
    root = Path(str(session_dir or "")).expanduser()
    if not str(root):
        raise ValueError("session_dir required for OSS segs root")
    segs = (root / OSS_SEGS_SUBDIR).resolve()
    segs.mkdir(parents=True, exist_ok=True)
    return segs


def canonical_seg_audio_path(segs_dir: str | Path, idx: int, *, ext: str = ".wav") -> Path:
    ext = ext if str(ext).startswith(".") else f".{ext}"
    return Path(segs_dir) / f"{int(idx):04d}{ext}"


def is_ghost_group_filename(name: str) -> bool:
    """True for relative ``*_g0000.mp3`` stamps that hid live pause_run files."""
    return bool(_GHOST_GROUP_RE.search(str(name or "")))


def resolve_tts_out_path(
    segs_dir: str | Path,
    idx: int,
    ctx_path: str | Path | None = None,
    *,
    ext: str = ".mp3",
) -> Path:
    """Write new TTS under segs/; reuse a live non-ghost file if it already exists."""
    segs = Path(segs_dir)
    segs.mkdir(parents=True, exist_ok=True)
    canonical = canonical_seg_audio_path(segs, idx, ext=ext)
    raw = str(ctx_path or "").strip()
    if not raw:
        return canonical
    p = Path(raw)
    try:
        if p.is_file() and p.stat().st_size >= OSS_MIN_AUDIO_BYTES and not is_ghost_group_filename(p.name):
            return p
    except OSError:
        pass
    return canonical


def stamp_canonical_paths(seg: dict[str, Any], abs_path: str | Path) -> None:
    abs_p = str(abs_path)
    for key in _PATH_KEYS:
        seg[key] = abs_p


def _write_silence_wav(out_path: Path, duration_ms: int, sample_rate: int = 24000) -> Path:
    ms = max(200, min(int(duration_ms or 1000), 30_000))
    frames = max(1, int(round((ms / 1000.0) * int(sample_rate))))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(int(sample_rate))
        fh.writeframes(b"\x00\x00" * frames)
    return out_path


def _usable(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        p = Path(str(path))
        return p.is_file() and p.stat().st_size >= OSS_MIN_AUDIO_BYTES
    except OSError:
        return False


def materialize_segment_into_segs(
    seg: dict[str, Any],
    segs_dir: str | Path,
    idx: int,
    *,
    slot_ms: int = 1000,
    resolve_path=None,
) -> Path:
    """Copy live audio (or write silence) to ``segs/{idx:04d}.wav`` and stamp keys.

    pyVideoTrans / VideoLingo: mux only reads this workdir; a bad sentence becomes
    a silence file, never a job abort.
    """
    segs = Path(segs_dir)
    segs.mkdir(parents=True, exist_ok=True)
    dest = canonical_seg_audio_path(segs, idx, ext=".wav")

    src = ""
    for key in _PATH_KEYS:
        raw = str(seg.get(key) or "").strip()
        if not raw:
            continue
        if resolve_path:
            try:
                raw = str(resolve_path(raw) or raw)
            except Exception:
                pass
        if _usable(raw) and not is_ghost_group_filename(Path(raw).name):
            src = raw
            break
        if _usable(raw) and not src:
            src = raw

    if src and _usable(src):
        src_p = Path(src)
        try:
            if src_p.resolve() != dest.resolve():
                shutil.copy2(str(src_p), str(dest))
        except OSError:
            if src_p.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac"):
                try:
                    shutil.copyfile(str(src_p), str(dest))
                except OSError:
                    src = ""
    if not _usable(dest):
        _write_silence_wav(dest, slot_ms)
        seg["audio_padded"] = True
        seg["silence_pad"] = True
        seg["pad_reason"] = seg.get("pad_reason") or "oss_missing_tts_pad"
        seg["duration_control_used"] = "soft_pad"
        try:
            seg["tts_ms"] = max(200, int(slot_ms or 1000))
            seg["playback_duration"] = seg["tts_ms"]
            seg["final_tts_duration_ms"] = seg["tts_ms"]
        except (TypeError, ValueError):
            pass

    try:
        abs_p = str(dest.resolve())
    except OSError:
        abs_p = str(dest)
    stamp_canonical_paths(seg, abs_p)
    seg["oss_segs_path"] = abs_p
    return dest


def canonicalize_session_artifacts(
    segments: list[dict[str, Any]] | None,
    session_dir: str | Path | None,
    *,
    task_info: dict[str, Any] | None = None,
    resolve_path=None,
) -> dict[str, Any]:
    """Copy every speakable clip into ``session_dir/segs/`` (VideoLingo workdir)."""
    segs_dir = resolve_oss_segs_dir(session_dir)
    copied = 0
    padded = 0
    skipped = 0
    paths: list[str] = []
    for idx, seg in enumerate(segments or []):
        if not isinstance(seg, dict):
            skipped += 1
            continue
        if seg.get("merged_into") is not None or seg.get("merged_into_id"):
            skipped += 1
            continue
        slot_ms = 1000
        try:
            st = int(seg.get("start_ms") or 0)
            en = int(seg.get("end_ms") or 0)
            if en > st:
                slot_ms = max(200, min(en - st, 30_000))
            elif int(seg.get("slot_ms") or 0) > 0:
                slot_ms = max(200, min(int(seg["slot_ms"]), 30_000))
        except (TypeError, ValueError):
            slot_ms = 1000
        before_pad = bool(seg.get("silence_pad") or seg.get("audio_padded"))
        dest = materialize_segment_into_segs(
            seg, segs_dir, idx, slot_ms=slot_ms, resolve_path=resolve_path
        )
        if seg.get("silence_pad") and not before_pad:
            padded += 1
        else:
            copied += 1
        paths.append(str(dest))
    stamp = {
        "oss_segs_dir": str(segs_dir),
        "oss_segs_copied": copied,
        "oss_segs_padded": padded,
        "oss_segs_skipped": skipped,
        "oss_mux_paths": paths,
    }
    if isinstance(task_info, dict):
        task_info.update(stamp)
        task_info["session_dir"] = str(Path(str(session_dir)).resolve()) if session_dir else task_info.get("session_dir")
    logger.info(
        "oss_production: segs=%s copied=%d padded=%d",
        segs_dir,
        copied,
        padded,
    )
    return stamp


def skip_missing_mix_inputs(
    paths: list[str | Path],
    *,
    slot_ms_list: list[int] | None = None,
    work_dir: str | Path | None = None,
) -> tuple[list[str], int]:
    """VideoLingo merge: missing file → silence pad, never abort the mix."""
    work = Path(work_dir) if work_dir else Path(".")
    work.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    padded = 0
    for i, raw in enumerate(paths or []):
        p = Path(str(raw or ""))
        if _usable(p):
            out.append(str(p))
            continue
        slot = 1000
        if slot_ms_list and i < len(slot_ms_list):
            try:
                slot = max(200, min(int(slot_ms_list[i] or 1000), 30_000))
            except (TypeError, ValueError):
                slot = 1000
        pad = work / f"oss_missing_{i:04d}.wav"
        _write_silence_wav(pad, slot)
        out.append(str(pad))
        padded += 1
        logger.warning("oss_production: skip-missing idx=%d → silence %dms", i, slot)
    return out, padded


def sequential_place_starts(
    starts_ms: list[int],
    durations_ms: list[int],
    *,
    min_gap_ms: int = OSS_MIN_GAP_MS,
    video_ms: int | None = None,
) -> list[int]:
    """SoniTranslate avoid_overlap: shift next start; never overlay two clips."""
    n = min(len(starts_ms), len(durations_ms))
    if n == 0:
        return []
    placed = [max(0, int(starts_ms[0]))]
    for i in range(1, n):
        prev_end = placed[i - 1] + max(0, int(durations_ms[i - 1] or 0))
        desired = max(0, int(starts_ms[i]))
        start = max(desired, prev_end + max(0, int(min_gap_ms)))
        placed.append(start)
    if video_ms and int(video_ms) > 0:
        cap = int(video_ms)
        for i in range(n):
            if placed[i] >= cap:
                placed[i] = max(0, cap - 1)
    return placed


def concat_sequential_track(
    clips: list[tuple[str | Path, int, int]],
    *,
    video_ms: int | None = None,
    min_gap_ms: int = OSS_MIN_GAP_MS,
    sample_rate: int = 24000,
):
    """VideoLingo merge_audio_segments: silence + clip + silence, pad to video.

    ``clips`` is ``(path, start_ms, duration_ms)``. Overlapping starts are shifted.
    """
    from pydub import AudioSegment

    if not clips:
        ms = max(1, int(video_ms or 1))
        return AudioSegment.silent(duration=ms, frame_rate=sample_rate)

    starts = sequential_place_starts(
        [int(s) for _, s, _ in clips],
        [int(d) for _, _, d in clips],
        min_gap_ms=min_gap_ms,
        video_ms=video_ms,
    )
    master = AudioSegment.silent(duration=0, frame_rate=sample_rate)
    cursor = 0
    for i, (path, _orig_start, _dur) in enumerate(clips):
        start = int(starts[i])
        if start > cursor:
            master += AudioSegment.silent(duration=start - cursor, frame_rate=sample_rate)
            cursor = start
        elif start < cursor:
            start = cursor
        try:
            clip = AudioSegment.from_file(str(path))
        except Exception:
            clip = AudioSegment.silent(duration=max(200, int(_dur or 200)), frame_rate=sample_rate)
        if clip.frame_rate != sample_rate:
            clip = clip.set_frame_rate(sample_rate)
        if clip.channels != 1:
            clip = clip.set_channels(1)
        if video_ms and cursor >= int(video_ms):
            break
        if video_ms and cursor + len(clip) > int(video_ms):
            clip = clip[: max(1, int(video_ms) - cursor)]
        master += clip
        cursor = len(master)

    vid = int(video_ms or 0)
    if vid > 0:
        if len(master) < vid:
            master += AudioSegment.silent(duration=vid - len(master), frame_rate=sample_rate)
        elif len(master) > vid:
            master = master[:vid]
    return master


def pad_master_to_video_ms(master, video_ms: int, *, sample_rate: int = 24000):
    """pyVideoTrans assembling: if audio < video, extend with silence (never crop tail first)."""
    from pydub import AudioSegment

    vid = int(video_ms or 0)
    if vid <= 0 or master is None:
        return master
    cur = len(master)
    if cur < vid:
        return master + AudioSegment.silent(duration=vid - cur, frame_rate=sample_rate)
    if cur > vid:
        return master[:vid]
    return master


def lock_voice_after_first_success(
    items: list[dict[str, Any]] | None,
    *,
    voice: str,
    engine_id: str,
) -> dict[str, Any]:
    """pyVideoTrans / VideoLingo: one backend+voice for the rest of the clip."""
    voice0 = str(voice or "").strip()
    engine0 = str(engine_id or "").strip() or "edge-offline"
    pinned = 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if voice0:
            if str(it.get("voice") or "") != voice0:
                pinned += 1
            it["voice"] = voice0
            it["assigned_voice"] = voice0
        if engine0:
            it["engine_id"] = engine0
    return {
        "oss_locked_voice": voice0,
        "oss_locked_engine": engine0,
        "oss_voice_pinned": pinned,
        "simple_voice_locked": True,
    }


def choose_duration_lever(tts_ms: int, slot_ms: int) -> str:
    """text rewrite → mild speed (0.9–1.1) → pad. Never cartoon atempo first."""
    slot = max(1, int(slot_ms or 0))
    tts = max(0, int(tts_ms or 0))
    delta = tts - slot
    ratio = float(tts) / float(slot)
    if abs(delta) > OSS_TEXT_DELTA_MS:
        if ratio > OSS_SPEED_MAX:
            return "text"
        if ratio < OSS_SPEED_MIN:
            return "text"
        if abs(delta) > OSS_SPEED_DELTA_MS and OSS_SPEED_MIN <= ratio <= OSS_SPEED_MAX:
            return "speed"
        return "text"
    if tts < slot:
        return "pad"
    if tts > slot and ratio <= OSS_SPEED_MAX and abs(delta) > OSS_SPEED_DELTA_MS:
        return "speed"
    return "none"


def clamp_oss_speed(atempo: float, *, default: float = 1.0) -> float:
    try:
        v = float(atempo)
    except (TypeError, ValueError):
        v = float(default)
    return max(OSS_SPEED_MIN, min(OSS_SPEED_MAX, v))
