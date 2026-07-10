"""Emotional tagging for segments — text heuristics + audio prosody (TZ §6)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EMOTION_KEYWORDS: dict[str, list[str]] = {
    "angry": [
        "!", "?!", "ненавижу", "черт", "блин", "идиот", "дурак",
        "hate", "damn", "idiot", "angry", "furious",
    ],
    "sad": [
        "...", "груст", "плач", "слёз", "смерт", "прощай",
        "sad", "cry", "tear", "goodbye", "sorry",
    ],
    "excited": [
        "!!!", "ура", "вау", "ого", "круто", "wow", "awesome", "yeah",
    ],
    "fear": [
        "боюсь", "страх", "помог", "опас", "afraid", "fear", "help", "danger",
    ],
    "question": [
        "?",
    ],
}

_TTS_PROFILE: dict[str, dict[str, str]] = {
    "neutral": {"rate": "-5%", "pitch": "+0Hz", "volume": "default"},
    "angry": {"rate": "+8%", "pitch": "+4Hz", "volume": "+10%"},
    "sad": {"rate": "-12%", "pitch": "-3Hz", "volume": "-5%"},
    "excited": {"rate": "+12%", "pitch": "+6Hz", "volume": "+8%"},
    "fear": {"rate": "+5%", "pitch": "+2Hz", "volume": "-3%"},
    "question": {"rate": "-3%", "pitch": "+3Hz", "volume": "default"},
}


@dataclass
class EmotionTag:
    emotion: str = "neutral"
    confidence: float = 0.5
    cues: list[str] = field(default_factory=list)
    intonation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "emotion": self.emotion,
            "confidence": round(self.confidence, 3),
            "cues": list(self.cues),
            "tts": dict(_TTS_PROFILE.get(self.emotion, _TTS_PROFILE["neutral"])),
            "intonation": dict(self.intonation),
        }


def _analyze_audio_prosody(
    audio_path: str | Path,
    *,
    start_ms: int = 0,
    end_ms: int | None = None,
) -> dict[str, Any]:
    """Energy / pitch proxy from segment audio slice."""
    out: dict[str, Any] = {"source": "none"}
    path = Path(audio_path)
    if not path.is_file():
        return out
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(str(path))
        if end_ms is not None and end_ms > start_ms:
            audio = audio[start_ms:end_ms]
        elif start_ms > 0:
            audio = audio[start_ms:]

        if len(audio) < 50:
            return out

        samples = audio.get_array_of_samples()
        if not samples:
            return out

        import math

        n = len(samples)
        rms = math.sqrt(sum(s * s for s in samples) / max(n, 1))
        peak = max(abs(s) for s in samples) or 1
        energy_db = 20 * math.log10(rms / max(peak, 1) + 1e-9)

        crossings = sum(
            1
            for i in range(1, min(n, 8000))
            if (samples[i] >= 0) != (samples[i - 1] >= 0)
        )
        zcr = crossings / max(min(n, 8000) - 1, 1)

        out = {
            "source": "audio",
            "energy_db": round(energy_db, 2),
            "zcr": round(zcr, 4),
            "duration_ms": len(audio),
        }
    except Exception as exc:
        logger.debug("emotion_tagger: prosody failed: %s", exc)
    return out


def _emotion_from_prosody(prosody: dict[str, Any]) -> tuple[str, float, list[str]]:
    if prosody.get("source") != "audio":
        return "neutral", 0.0, []
    cues: list[str] = []
    energy = float(prosody.get("energy_db") or -40)
    zcr = float(prosody.get("zcr") or 0)
    scores: dict[str, float] = {"neutral": 0.2}

    if energy > -18:
        scores["angry"] = scores.get("angry", 0) + 0.45
        scores["excited"] = scores.get("excited", 0) + 0.35
        cues.append("high_energy")
    elif energy < -32:
        scores["sad"] = scores.get("sad", 0) + 0.4
        cues.append("low_energy")

    if zcr > 0.12:
        scores["excited"] = scores.get("excited", 0) + 0.25
        scores["fear"] = scores.get("fear", 0) + 0.15
        cues.append("high_zcr")
    elif zcr < 0.04:
        scores["sad"] = scores.get("sad", 0) + 0.2
        cues.append("low_zcr")

    best = max(scores.items(), key=lambda x: x[1])
    if best[1] < 0.35:
        return "neutral", 0.55, cues
    return best[0], min(0.9, 0.4 + best[1]), cues


def classify_segment(
    text: str,
    *,
    original: str = "",
    audio_path: str | Path | None = None,
    audio_start_ms: int = 0,
    audio_end_ms: int | None = None,
) -> EmotionTag:
    t = (text or "").strip()
    text_tag = _classify_text(t, original=original)
    if audio_path:
        prosody = _analyze_audio_prosody(
            audio_path, start_ms=audio_start_ms, end_ms=audio_end_ms
        )
        audio_em, audio_conf, audio_cues = _emotion_from_prosody(prosody)
        if audio_conf > text_tag.confidence:
            return EmotionTag(
                emotion=audio_em,
                confidence=audio_conf,
                cues=(audio_cues + text_tag.cues)[:6],
                intonation=prosody,
            )
        text_tag.intonation = prosody
        text_tag.cues = (text_tag.cues + audio_cues)[:6]
    return text_tag


def _classify_text(text: str, *, original: str = "") -> EmotionTag:
    t = (text or "").strip()
    if not t:
        return EmotionTag()
    scores: dict[str, float] = {k: 0.0 for k in _EMOTION_KEYWORDS}
    lower = t.lower()
    cues: list[str] = []
    for emotion, keys in _EMOTION_KEYWORDS.items():
        for kw in keys:
            if kw in ("!", "?", "?!", "!!!", "..."):
                if kw in t:
                    scores[emotion] += 0.35
                    cues.append(kw)
            elif kw.lower() in lower:
                scores[emotion] += 0.5
                cues.append(kw)
    if re.search(r"[A-ZА-ЯЁ]{3,}", t):
        scores["excited"] = scores.get("excited", 0) + 0.2
        cues.append("CAPS")
    best = max(scores.items(), key=lambda x: x[1])
    if best[1] < 0.35:
        return EmotionTag(emotion="neutral", confidence=0.6)
    conf = min(0.95, 0.45 + best[1])
    return EmotionTag(emotion=best[0], confidence=conf, cues=cues[:5])


def classify_segments(
    texts: list[str],
    *,
    originals: list[str] | None = None,
    audio_paths: list[str | None] | None = None,
    timing_map: list[Any] | None = None,
) -> list[EmotionTag]:
    origs = originals or []
    paths = audio_paths or []
    out: list[EmotionTag] = []
    for i, t in enumerate(texts):
        start_ms = 0
        end_ms = None
        if timing_map and i < len(timing_map):
            tm = timing_map[i]
            if isinstance(tm, dict):
                start_ms = int(tm.get("start", tm.get("start_ms", 0)))
                end_ms = int(tm.get("end", tm.get("end_ms", 0)))
            elif isinstance(tm, (list, tuple)) and len(tm) >= 2:
                start_ms, end_ms = int(tm[0]), int(tm[1])
        ap = paths[i] if i < len(paths) else None
        out.append(
            classify_segment(
                t,
                original=origs[i] if i < len(origs) else "",
                audio_path=ap,
                audio_start_ms=start_ms,
                audio_end_ms=end_ms,
            )
        )
    return out


def tts_params_for_emotion(tag: EmotionTag | dict[str, Any]) -> dict[str, str]:
    if isinstance(tag, dict):
        em = str(tag.get("emotion") or "neutral")
        return dict(_TTS_PROFILE.get(em, _TTS_PROFILE["neutral"]))
    return dict(_TTS_PROFILE.get(tag.emotion, _TTS_PROFILE["neutral"]))


def is_emotion_tts_enabled() -> bool:
    from engines.core.feature_flags import is_enabled

    return is_enabled("emotion_tts", developer_session=True)


def apply_emotion_to_segment(segment: dict[str, Any], emotion: str) -> dict[str, Any]:
    """Set emotion on segment JSON and attach intonation/TTS metadata."""
    em = (emotion or "neutral").lower()
    segment["emotion"] = em
    tag = EmotionTag(emotion=em, confidence=0.85, cues=["manual"])
    segment["tts_emotion"] = tag.to_dict()
    segment["intonation"] = tag.intonation
    return segment
