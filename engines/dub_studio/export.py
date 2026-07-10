"""Export Dub Studio timeline to mixed audio."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from engines.dub_studio.models import DubProject


def export_timeline_wav(project: DubProject, app_dir: Path, *, format: str = "wav") -> Path:
    from pydub import AudioSegment

    base = app_dir / "output" / "dub_studio" / project.project_id / "export"
    base.mkdir(parents=True, exist_ok=True)
    duration = max(int(project.duration_ms or 0), 1)
    canvas = AudioSegment.silent(duration=duration)

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
        pos = max(0, int(seg.hard_anchor_ms or seg.start_ms or 0))
        canvas = canvas.overlay(clip, position=pos)

    ext = "mp3" if format.lower() == "mp3" else "wav"
    out = base / f"export_{int(time.time())}_{uuid.uuid4().hex[:6]}.{ext}"
    if ext == "mp3":
        canvas.export(str(out), format="mp3", bitrate="192k")
    else:
        canvas.export(str(out), format="wav")
    return out
