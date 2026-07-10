"""Emotional tagging — extract + TTS bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.dub_studio.models import EmotionTag

# Emotion → TTS parameters (edge-tts / generic engines)
EMOTION_TTS_MAP: dict[str, dict[str, Any]] = {
    EmotionTag.NEUTRAL.value: {"rate": "+0%", "pitch": "+0Hz", "volume": "+0%", "stability": 0.7},
    EmotionTag.HAPPY.value: {"rate": "+8%", "pitch": "+4Hz", "volume": "+4%", "stability": 0.5},
    EmotionTag.ANGRY.value: {"rate": "+12%", "pitch": "+2Hz", "volume": "+10%", "stability": 0.35},
    EmotionTag.SAD.value: {"rate": "-10%", "pitch": "-4Hz", "volume": "-6%", "stability": 0.8},
    EmotionTag.WHISPER.value: {"rate": "-6%", "pitch": "-2Hz", "volume": "-14%", "stability": 0.9},
    EmotionTag.SHOUTING.value: {"rate": "+6%", "pitch": "+6Hz", "volume": "+16%", "stability": 0.3},
    EmotionTag.IRONIC.value: {"rate": "+4%", "pitch": "+3Hz", "volume": "+2%", "stability": 0.45},
}


def extract_emotion(
    audio_path: Path | None,
    *,
    start_ms: int = 0,
    end_ms: int = 0,
) -> dict[str, Any]:
    """
    Emotion Extractor stub — Stage 2: Wav2Vec2 / emotion recognition model.
    Heuristic: RMS + spectral centroid proxy from pydub.
    """
    if not audio_path or not Path(audio_path).is_file():
        return {"emotion": EmotionTag.NEUTRAL.value, "confidence": 0.0, "source": "none"}

    try:
        from pydub import AudioSegment

        clip = AudioSegment.from_file(str(audio_path))[start_ms:end_ms]
        if len(clip) < 80:
            return {"emotion": EmotionTag.NEUTRAL.value, "confidence": 0.2, "source": "short"}

        rms = clip.dBFS
        if rms < -35:
            tag = EmotionTag.WHISPER.value
        elif rms > -8:
            tag = EmotionTag.SHOUTING.value
        elif rms > -14:
            tag = EmotionTag.ANGRY.value
        elif rms < -22:
            tag = EmotionTag.SAD.value
        else:
            tag = EmotionTag.NEUTRAL.value
        conf = min(0.85, max(0.35, abs(rms + 18) / 30))
        return {"emotion": tag, "confidence": round(conf, 2), "source": "heuristic_rms"}
    except Exception as e:
        return {"emotion": EmotionTag.NEUTRAL.value, "confidence": 0.0, "source": f"error:{e}"}


def emotion_to_tts_params(emotion: str) -> dict[str, Any]:
    key = (emotion or EmotionTag.NEUTRAL.value).upper()
    base = dict(EMOTION_TTS_MAP.get(key, EMOTION_TTS_MAP[EmotionTag.NEUTRAL.value]))
    base["emotion_tag"] = key
    return base


def apply_emotion_bridge(segment_meta: dict[str, Any], emotion: str) -> dict[str, Any]:
    params = emotion_to_tts_params(emotion)
    segment_meta = dict(segment_meta or {})
    segment_meta["emotion"] = emotion
    segment_meta["tts_params"] = params
    return segment_meta
