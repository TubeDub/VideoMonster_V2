"""P8 Timing Planner + P9 Audio Predictor."""

from __future__ import annotations

from engines.semantic_v3.types import SemanticSentence

# Approx ms per syllable by language family (deterministic predictor)
_MS_PER_SYLLABLE = {
    "en": 180,
    "uk": 200,
    "ru": 195,
    "de": 190,
    "default": 185,
}


def plan_sentence_timing(sent: SemanticSentence, *, tgt_lang: str = "uk") -> SemanticSentence:
    """P8 — ideal duration from slot + prosody hints."""
    slot = sent.slot_ms
    sent.ideal_duration_ms = max(200, int(slot * 0.92)) if slot > 0 else 0
    # Emotion affects target rate slightly
    if sent.emotion in ("angry", "excited"):
        sent.speech_rate = 1.05
    elif sent.emotion in ("sad", "calm"):
        sent.speech_rate = 0.95
    else:
        sent.speech_rate = 1.0
    return sent


def predict_tts_ms(sent: SemanticSentence, *, tgt_lang: str = "uk") -> int:
    """P9 — estimate duration BEFORE TTS from syllables/SSML-ish factors."""
    text = (sent.translated_text or sent.text or "").strip()
    if not text:
        return 0
    syllables = sum(
        max(1, sum(1 for c in w.lower() if c in "aeiouyаеєиіїоуюяё"))
        for w in text.split()
    )
    base = _MS_PER_SYLLABLE.get(tgt_lang[:2].lower(), _MS_PER_SYLLABLE["default"])
    ms = int(syllables * base / max(0.8, sent.speech_rate))
    # Punctuation breathing
    ms += text.count(",") * 120
    ms += text.count(".") * 180
    ms += text.count("?") * 200
    ms += text.count("!") * 160
    return max(200, ms)


def apply_audio_predictor(
    sentences: list[SemanticSentence],
    *,
    tgt_lang: str = "uk",
) -> list[SemanticSentence]:
    for s in sentences:
        plan_sentence_timing(s, tgt_lang=tgt_lang)
        s.predicted_tts_ms = predict_tts_ms(s, tgt_lang=tgt_lang)
        slot = s.slot_ms
        if slot > 0 and s.predicted_tts_ms > int(slot * 1.15):
            s.overflow_ms = s.predicted_tts_ms - slot
            s.recovery_plan = [
                "trim_silence",
                "ssml_prosody",
                "tempo",
                "micro_stretch",
                "borrow_time",
                "sentence_merge",
                "semantic_rewrite",
            ]
        elif slot > 0 and s.predicted_tts_ms < int(slot * 0.85):
            s.underflow_ms = slot - s.predicted_tts_ms
            s.recovery_plan = ["tempo_down", "natural_pause", "breath", "silence_padding"]
    return sentences


def should_block_tts(sent: SemanticSentence) -> bool:
    """P9 — if prediction cannot fit and no recovery left, do not launch TTS."""
    if sent.slot_ms <= 0:
        return False
    # Block only extreme overflow without merge room (>40%)
    return sent.predicted_tts_ms > int(sent.slot_ms * 1.40) and not sent.recovery_plan
