"""Regenerate segment TTS with emotion parameters."""

from __future__ import annotations

import uuid
from pathlib import Path

from engines.dub_studio.emotion import emotion_to_tts_params
from engines.dub_studio.models import StudioSegment


def regenerate_segment_audio(
    app_dir: Path,
    project_id: str,
    seg: StudioSegment,
    *,
    voice: str | None = None,
) -> tuple[Path, int]:
    from engines.tts import DEFAULT_VOICE, generate_audio

    params = seg.tts_params or emotion_to_tts_params(seg.emotion)
    rate = str(params.get("rate") or "+0%")
    pitch = params.get("pitch")
    v = voice or str(seg.meta.get("voice") or DEFAULT_VOICE)

    out_dir = app_dir / "output" / "dub_studio" / project_id / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{seg.segment_id}_{uuid.uuid4().hex[:8]}.mp3"
    out_path = out_dir / fname

    # generate_audio writes to engines.tts.OUTPUT_DIR — copy approach via direct call
    files = generate_audio(
        seg.text or " ",
        voice=v,
        rate=rate,
        pitch=str(pitch) if pitch else None,
    )
    if not files:
        raise RuntimeError("TTS produced no files")

    src = app_dir / "output" / files[0]
    if not src.is_file():
        raise RuntimeError("TTS output missing")

    data = src.read_bytes()
    out_path.write_bytes(data)
    try:
        from pydub import AudioSegment

        tts_ms = len(AudioSegment.from_file(str(out_path)))
    except Exception:
        tts_ms = 0
    return out_path, tts_ms
