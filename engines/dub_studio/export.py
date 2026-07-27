"""Export Dub Studio timeline to mixed audio."""

from __future__ import annotations

import math
import time
import uuid
from pathlib import Path

from engines.dub_studio.models import DubProject, StudioTrack


def track_effective_gain(tracks: list[StudioTrack], track: StudioTrack | None) -> float:
    """Resolve mute / solo / volume into a linear gain (0..2)."""
    if track is None:
        return 1.0
    if bool(track.muted):
        return 0.0
    any_solo = any(bool(t.solo) for t in tracks)
    if any_solo and not bool(track.solo):
        return 0.0
    try:
        vol = float(track.volume if track.volume is not None else 1.0)
    except (TypeError, ValueError):
        vol = 1.0
    return max(0.0, min(2.0, vol))


def _apply_linear_gain(clip, gain: float):
    if gain <= 0.0:
        from pydub import AudioSegment

        return AudioSegment.silent(duration=len(clip))
    if abs(gain - 1.0) < 1e-6:
        return clip
    # pydub apply_gain expects dB
    return clip.apply_gain(20.0 * math.log10(gain))


def _find_track(project: DubProject, kind: str) -> StudioTrack | None:
    return next((t for t in project.tracks if t.kind == kind), None)


def export_timeline_wav(project: DubProject, app_dir: Path, *, format: str = "wav") -> Path:
    from pydub import AudioSegment

    base = app_dir / "output" / "dub_studio" / project.project_id / "export"
    base.mkdir(parents=True, exist_ok=True)
    duration = max(int(project.duration_ms or 0), 1)
    canvas = AudioSegment.silent(duration=duration)

    tts_track = _find_track(project, "tts")
    tts_gain = track_effective_gain(project.tracks, tts_track)

    if tts_gain > 0.0:
        for seg in project.segments:
            ver = next((v for v in seg.versions if v.version_id == seg.active_version_id), None)
            if not ver or not ver.audio_path:
                continue
            p = Path(ver.audio_path)
            if not p.is_file():
                continue
            try:
                clip = AudioSegment.from_file(str(p))
            except Exception:
                continue
            clip = _apply_linear_gain(clip, tts_gain)
            pos = max(0, int(seg.hard_anchor_ms or seg.start_ms or 0))
            canvas = canvas.overlay(clip, position=pos)

    # Optional clip-based tracks (music / original / user_voice / fx / aux)
    for track in project.tracks:
        if track.kind == "tts":
            continue
        gain = track_effective_gain(project.tracks, track)
        if gain <= 0.0:
            continue
        for clip_meta in track.clips or []:
            audio_path = str(clip_meta.get("audio_path") or clip_meta.get("path") or "").strip()
            if not audio_path:
                continue
            p = Path(audio_path)
            if not p.is_file():
                continue
            try:
                clip = AudioSegment.from_file(str(p))
            except Exception:
                continue
            clip = _apply_linear_gain(clip, gain)
            pos = max(0, int(clip_meta.get("start_ms") or clip_meta.get("position_ms") or 0))
            canvas = canvas.overlay(clip, position=pos)

    ext = "mp3" if format.lower() == "mp3" else "wav"
    out = base / f"export_{int(time.time())}_{uuid.uuid4().hex[:6]}.{ext}"
    if ext == "mp3":
        canvas.export(str(out), format="mp3", bitrate="192k")
    else:
        canvas.export(str(out), format="wav")
    return out
