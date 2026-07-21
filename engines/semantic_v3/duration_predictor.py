"""P38 Speech Duration Predictor — phoneme/syllable/TTS profile based (not chars)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from engines.semantic_v3.phoneme_viseme import analyze_word_phonemes
from engines.semantic_v3.target_duration_engine import target_ms_for
from engines.semantic_v3.types import SemanticSentence

# Voice profile history (deterministic defaults; learning fills later)
_VOICE_MS_PER_PHONE: dict[str, float] = {
    "default": 70.0,
    "uk-UA-OstapNeural": 72.0,
    "uk-UA-PolinaNeural": 68.0,
    "en-US-GuyNeural": 65.0,
}


@dataclass(frozen=True)
class ExpectedSpeechDuration:
    expected_ms: int
    confidence: float
    method: str = "phoneme"
    phone_count: int = 0
    syllable_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def predict_speech_duration(
    text: str,
    *,
    voice: str = "default",
    emotion: str = "neutral",
    speech_rate: float = 1.0,
    ssml_rate: float = 1.0,
) -> ExpectedSpeechDuration:
    words = str(text or "").split()
    phones = 0
    syllables = 0
    phone_ms = 0.0
    for w in words:
        ph = analyze_word_phonemes(w, duration_ms=200, speech_rate=speech_rate)
        phones += len(ph)
        syllables += max(1, sum(1 for c in w.lower() if c in "aeiouyаеєиіїоуюяё"))
        phone_ms += sum(p.duration_ms for p in ph)

    base = _VOICE_MS_PER_PHONE.get(voice, _VOICE_MS_PER_PHONE["default"])
    # Blend phoneme sum with voice profile
    expected = phone_ms * 0.55 + phones * base * 0.45
    # Emotion / SSML
    emo = {"angry": 0.92, "excited": 0.90, "sad": 1.08, "calm": 1.05}.get(emotion, 1.0)
    expected *= emo / max(0.7, speech_rate * ssml_rate)
    # Punctuation breaths
    expected += text.count(",") * 110
    expected += text.count(".") * 160
    expected += text.count("?") * 180
    expected += text.count("!") * 150

    conf = 0.55
    if phones >= 8:
        conf = 0.75
    if phones >= 20:
        conf = 0.85

    return ExpectedSpeechDuration(
        expected_ms=max(180, int(round(expected))),
        confidence=conf,
        method="phoneme_voice_profile",
        phone_count=phones,
        syllable_count=syllables,
    )


def apply_duration_predictor(
    sentences: list[SemanticSentence],
    *,
    voice: str = "default",
    tgt_lang: str = "uk",
) -> list[SemanticSentence]:
    for s in sentences:
        text = s.translated_text or s.text
        pred = predict_speech_duration(
            text,
            voice=voice,
            emotion=s.emotion,
            speech_rate=s.speech_rate or 1.0,
        )
        s.predicted_tts_ms = pred.expected_ms
        setattr(s, "prediction_confidence", pred.confidence)
        setattr(s, "duration_prediction", pred.to_dict())
        slot = target_ms_for(s)
        s.overflow_ms = 0
        s.underflow_ms = 0
        if slot > 0:
            target_meta = getattr(s, "target_duration", None)
            tolerance_ms = (
                int(target_meta.get("tolerance_ms") or 0)
                if isinstance(target_meta, dict)
                else 0
            )
            tolerance_ms = max(tolerance_ms, int(slot * 0.12))
            if pred.expected_ms > slot + tolerance_ms:
                s.overflow_ms = pred.expected_ms - slot
            elif pred.expected_ms < slot - tolerance_ms:
                s.underflow_ms = slot - pred.expected_ms
    return sentences
