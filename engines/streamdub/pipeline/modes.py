"""StreamDub processing modes — stage graphs."""

from __future__ import annotations

from engines.streamdub.types import StreamDubMode

MODE_STAGES: dict[StreamDubMode, list[str]] = {
    StreamDubMode.FAST: [
        "whisper",
        "segmenter",
        "fast_translation",
        "tts",
        "video_merge",
    ],
    StreamDubMode.SMART: [
        "whisper",
        "segmenter",
        "fast_translation",
        "quality_analyzer",
        "llm_refiner",
        "tts",
        "video_merge",
    ],
    StreamDubMode.CINEMA: [
        "whisper",
        "segmenter",
        "llm_refiner",
        "quality_analyzer",
        "voice_clone",
        "lip_sync",
        "tts",
        "video_merge",
    ],
}


def stages_for_mode(mode: StreamDubMode) -> list[str]:
    return list(MODE_STAGES.get(mode, MODE_STAGES[StreamDubMode.SMART]))
