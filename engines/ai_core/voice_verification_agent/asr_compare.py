"""ASR-based voice verification — compare expected text vs transcribed WAV."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from engines.mt.lang_codes import normalize_lang
from engines.pipeline_language_gate import is_critical_language_mismatch

logger = logging.getLogger("tubedub.ai_core.voice_verification.asr_compare")

SIMILARITY_PASS = 0.82
MISSING_WORDS_RATIO_FAIL = 0.22
TRUNCATED_TAIL_WORDS_FAIL = 2
AUDIO_COMPLETENESS_MIN = 0.50
AUDIO_COMPLETENESS_MAX = 1.35
ASR_CONFIDENCE_PASS = 0.55
MAX_VERIFICATION_CYCLES = 3

_WORD_RE = re.compile(r"[\w\u0400-\u04FF\u0500-\u052F'-]+", re.UNICODE)


def normalize_words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(str(text or "")) if w.strip()]


def text_similarity(expected: str, recognized: str) -> float:
    a = " ".join(normalize_words(expected))
    b = " ".join(normalize_words(recognized))
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return float(SequenceMatcher(None, a, b).ratio())


def missing_words(expected: str, recognized: str) -> list[str]:
    exp = normalize_words(expected)
    rec = set(normalize_words(recognized))
    return [w for w in exp if w not in rec]


def truncated_words(expected: str, recognized: str) -> list[str]:
    """Words in expected that appear cut off in ASR (prefix match only)."""
    exp = normalize_words(expected)
    rec = normalize_words(recognized)
    if not exp or not rec:
        return []
    truncated: list[str] = []
    for i, ew in enumerate(exp):
        if i >= len(rec):
            truncated.append(ew)
            continue
        rw = rec[i]
        if ew != rw and ew.startswith(rw) and len(rw) >= 2:
            truncated.append(ew)
        elif ew != rw and rw.startswith(ew) and len(ew) >= 2:
            continue
    tail_missing = exp[len(rec) :] if len(rec) < len(exp) else []
    for w in tail_missing:
        if w not in truncated:
            truncated.append(w)
    return truncated


def transcribe_wav_for_verification(
    wav_path: Path,
    *,
    language: str,
    model_size: str = "tiny",
) -> tuple[str, float, str]:
    """Return (recognized_text, confidence 0..1, detected_lang)."""
    path = Path(wav_path)
    if not path.is_file():
        return "", 0.0, ""

    try:
        from engines.stt_engine import check_available, transcribe

        ok, _engine = check_available()
        if not ok:
            return "", 0.0, ""
        text, _srt, _timing, detected = transcribe(
            str(path),
            language=normalize_lang(language) or None,
            model_size=model_size,
            word_timestamps=False,
        )
        conf = min(1.0, max(0.0, text_similarity(text, text)))
        if text.strip():
            conf = 0.75
        return str(text or "").strip(), conf, str(detected or language)
    except Exception as exc:
        logger.debug("ASR verification skipped for %s: %s", path, exc)
        return "", 0.0, ""


def audio_duration_ms(wav_path: Path) -> int:
    try:
        from pydub import AudioSegment

        return int(len(AudioSegment.from_file(str(wav_path))))
    except Exception:
        return 0


@dataclass
class VerificationMetrics:
    similarity: float = 1.0
    missing_words: list[str] = field(default_factory=list)
    truncated_words: list[str] = field(default_factory=list)
    language_match: bool = True
    audio_completeness: float = 1.0
    asr_confidence: float = 0.0
    recognized_text: str = ""
    issues: list[str] = field(default_factory=list)
    route_to: str = ""

    @property
    def passed(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "similarity": round(self.similarity, 4),
            "missing_words": self.missing_words,
            "truncated_words": self.truncated_words,
            "language_match": self.language_match,
            "audio_completeness": round(self.audio_completeness, 4),
            "asr_confidence": round(self.asr_confidence, 4),
            "recognized_text": self.recognized_text,
            "issues": self.issues,
            "route_to": self.route_to,
            "pass": self.passed,
        }


def verify_segment_audio(
    *,
    expected_text: str,
    wav_path: Path,
    target_lang: str,
    slot_ms: int | None = None,
    source_text: str = "",
    asr_text: str | None = None,
    asr_confidence: float | None = None,
) -> VerificationMetrics:
    """Compare expected dub text with ASR of synthesized WAV."""
    metrics = VerificationMetrics()
    expected = str(expected_text or "").strip()
    if not expected:
        metrics.issues.append("empty_expected_text")
        metrics.route_to = "semantic"
        return metrics

    if not wav_path.is_file():
        metrics.issues.append("missing_wav")
        metrics.route_to = "voice"
        return metrics

    recognized = str(asr_text or "").strip()
    confidence = float(asr_confidence or 0.0)
    if not recognized:
        recognized, confidence, _detected = transcribe_wav_for_verification(
            wav_path,
            language=target_lang,
        )
    metrics.asr_confidence = confidence
    metrics.recognized_text = recognized

    if not recognized:
        metrics.issues.append("asr_empty")
        metrics.route_to = "voice"
        metrics.similarity = 0.0
        return metrics

    metrics.similarity = text_similarity(expected, recognized)
    metrics.missing_words = missing_words(expected, recognized)
    metrics.truncated_words = truncated_words(expected, recognized)

    lang_bad, _code = is_critical_language_mismatch(
        recognized,
        target_lang=target_lang,
        original=source_text or expected,
    )
    metrics.language_match = not lang_bad

    playback_ms = audio_duration_ms(wav_path)
    ref_ms = slot_ms or playback_ms or 1
    metrics.audio_completeness = playback_ms / max(1, ref_ms)

    exp_words = normalize_words(expected)
    missing_ratio = len(metrics.missing_words) / max(1, len(exp_words))

    if not metrics.language_match:
        metrics.issues.append("language_mismatch")

    if missing_ratio >= MISSING_WORDS_RATIO_FAIL:
        metrics.issues.append("missing_words")

    if len(metrics.truncated_words) >= 1:
        metrics.issues.append("truncated_words")

    tail_missing = metrics.missing_words[-TRUNCATED_TAIL_WORDS_FAIL:]
    if tail_missing and metrics.similarity < SIMILARITY_PASS:
        metrics.issues.append("truncated_sentence")

    if metrics.audio_completeness < AUDIO_COMPLETENESS_MIN:
        metrics.issues.append("audio_incomplete")

    if metrics.audio_completeness > AUDIO_COMPLETENESS_MAX:
        metrics.issues.append("duration_overflow")

    if metrics.similarity < SIMILARITY_PASS:
        metrics.issues.append("low_similarity")

    if metrics.asr_confidence < ASR_CONFIDENCE_PASS and metrics.similarity < SIMILARITY_PASS:
        metrics.issues.append("low_asr_confidence")

    metrics.route_to = route_verification_failure(metrics, expected=expected, source=source_text)
    return metrics


def route_verification_failure(
    metrics: VerificationMetrics,
    *,
    expected: str,
    source: str,
) -> str:
    """Map verification issues to responsible upstream agent."""
    issues = set(metrics.issues)

    if "empty_expected_text" in issues or "language_mismatch" in issues:
        return "semantic"
    if "missing_words" in issues:
        from engines.dub_quality_stabilization import basic_meaning_preserved

        if source and not basic_meaning_preserved(source, expected):
            return "semantic"
        if len(metrics.missing_words) >= 3:
            return "semantic"

    if "truncated_words" in issues and metrics.similarity >= 0.65:
        return "grammar"

    if "duration_overflow" in issues or (
        "audio_incomplete" in issues and metrics.audio_completeness < 0.75
    ):
        return "timing"

    if "truncated_sentence" in issues and metrics.audio_completeness >= 0.85:
        return "timing"

    if issues & {"missing_wav", "asr_empty", "low_similarity", "low_asr_confidence", "audio_incomplete"}:
        return "voice"

    if "truncated_words" in issues or "truncated_sentence" in issues:
        return "voice" if metrics.audio_completeness >= 0.80 else "timing"

    return "voice"
