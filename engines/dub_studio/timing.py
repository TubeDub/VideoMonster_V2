"""Studio Timing & Anchoring — Hard-Anchor + Adaptive Container."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from engines.dub_studio.models import ContainerStatus, StudioSegment


def container_fill_percent(tts_ms: int, container_ms: int) -> float:
    if container_ms <= 0:
        return 0.0
    return round(100.0 * tts_ms / container_ms, 1)


def container_status(fill_pct: float) -> str:
    if fill_pct > 100:
        return ContainerStatus.RED.value
    if fill_pct >= 90:
        return ContainerStatus.YELLOW.value
    return ContainerStatus.GREEN.value


def apply_hard_anchor(segment: StudioSegment, *, first_word_ms: int | None = None) -> StudioSegment:
    """Hard-Anchor: first word locked to original timeline start."""
    anchor = int(first_word_ms if first_word_ms is not None else segment.start_ms)
    segment.hard_anchor_ms = anchor
    segment.container_ms = max(0, segment.end_ms - segment.start_ms)
    return segment


def compute_stretch_ratio(tts_ms: int, container_ms: int, *, max_stretch: float = 1.35) -> float:
    if container_ms <= 0 or tts_ms <= 0:
        return 1.0
    if tts_ms <= container_ms:
        return 1.0
    need = tts_ms / container_ms
    return min(max_stretch, max(1.0, need))


def time_stretch_audio(
    input_path: Path,
    output_path: Path,
    *,
    ratio: float,
) -> dict[str, Any]:
    """Time stretch without pitch change (ffmpeg atempo, capped chain)."""
    ratio = max(1.0, float(ratio))
    if ratio <= 1.001:
        shutil.copy2(input_path, output_path)
        return {"ratio": 1.0, "applied": False}

    atempo = min(2.0, ratio)
    chains: list[str] = []
    rem = ratio
    while rem > 1.001:
        step = min(2.0, rem)
        chains.append(f"atempo={step:.4f}")
        rem /= step
    af = ",".join(chains) if chains else f"atempo={atempo:.4f}"

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    subprocess.run(
        [ffmpeg, "-y", "-i", str(input_path), "-af", af, str(output_path)],
        check=True,
        capture_output=True,
    )
    from pydub import AudioSegment

    out_ms = len(AudioSegment.from_file(str(output_path)))
    return {"ratio": ratio, "applied": True, "output_ms": out_ms, "filter": af}


def update_segment_timing(
    segment: StudioSegment,
    *,
    tts_ms: int,
    max_stretch: float = 1.35,
) -> StudioSegment:
    segment.tts_ms = int(tts_ms)
    segment.container_ms = max(0, segment.end_ms - segment.start_ms)
    fill = container_fill_percent(segment.tts_ms, segment.container_ms)
    segment.container_status = container_status(fill)
    segment.stretch_ratio = compute_stretch_ratio(
        segment.tts_ms, segment.container_ms, max_stretch=max_stretch
    )
    segment.meta["fill_percent"] = fill
    return segment
