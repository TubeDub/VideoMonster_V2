"""AI Core — Project Analysis.

AI Core watches the whole project first and forms a single understanding of it
before any segment is processed. From the transcript + timing it derives:
language, content type (film / blog / documentary / interview …), speaking
tempo, dialogue density, emotional tone, number of distinct speakers (best
effort), and text complexity.

This is deliberately dependency-light (no heavy ML) so it always runs: it reuses
existing heuristics (``content_mode.auto_detect_mode``) and cheap text/timing
statistics. Richer signals (audio prosody, diarization) can be layered in later
without changing the ``ProjectProfile`` contract.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

# Words-per-second thresholds used to bucket speaking tempo.
_TEMPO_SLOW = 2.0
_TEMPO_FAST = 3.4


@dataclass
class ProjectProfile:
    """AI Core's understanding of the whole project."""

    source_lang: str = ""
    target_lang: str = ""
    content_type: str = "movie"      # movie/blogger/podcast/interview/…
    genre: str = "general"           # coarse: narrative/talk/educational/…
    speech_style: str = "neutral"    # neutral/casual/formal/dramatic
    dominant_emotion: str = "neutral"
    tempo: str = "medium"            # slow/medium/fast
    words_per_second: float = 0.0
    dialogue_density: float = 0.0     # fraction of time filled with speech
    speaker_count: int = 1
    complexity: str = "medium"        # low/medium/high
    complexity_score: float = 0.0
    segment_count: int = 0
    total_duration_ms: int = 0
    avg_segment_ms: float = 0.0
    avg_gap_ms: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _slot_bounds_ms(timing_row: Any) -> tuple[int, int] | None:
    """Extract (start_ms, end_ms) from a heterogeneous timing entry."""
    if timing_row is None:
        return None
    start = end = None
    if isinstance(timing_row, dict):
        start = timing_row.get("start_ms", timing_row.get("start"))
        end = timing_row.get("end_ms", timing_row.get("end"))
    else:
        start = getattr(timing_row, "start_ms", getattr(timing_row, "start", None))
        end = getattr(timing_row, "end_ms", getattr(timing_row, "end", None))
    try:
        s = int(float(start))
        e = int(float(end))
    except (TypeError, ValueError):
        return None
    if e < s:
        s, e = e, s
    return s, e


def _word_count(text: str) -> int:
    return len(str(text or "").split())


def _detect_emotion(texts: Sequence[str]) -> str:
    """Cheap emotional tone from punctuation/markers across the transcript."""
    joined = " ".join(str(t or "") for t in texts)
    if not joined.strip():
        return "neutral"
    excl = joined.count("!")
    ques = joined.count("?")
    words = max(1, _word_count(joined))
    excl_ratio = excl / words
    ques_ratio = ques / words
    if excl_ratio > 0.02:
        return "energetic"
    if ques_ratio > 0.03:
        return "inquisitive"
    if re.search(r"\b(sad|sorry|cry|loss|grief|печал|груст|сумн)\b", joined, re.I):
        return "somber"
    return "neutral"


def _detect_complexity(texts: Sequence[str]) -> tuple[str, float]:
    """Text complexity from avg sentence length + long-word ratio."""
    joined = " ".join(str(t or "") for t in texts)
    words = joined.split()
    if not words:
        return "low", 0.0
    long_words = sum(1 for w in words if len(w) >= 9)
    long_ratio = long_words / len(words)
    sentences = [s for s in re.split(r"[.!?]+", joined) if s.strip()]
    avg_sentence_words = len(words) / max(1, len(sentences))
    # Normalise into 0..1: long sentences (>22 words) and many long words = hard.
    score = min(1.0, 0.5 * min(1.0, avg_sentence_words / 22.0) + 0.5 * min(1.0, long_ratio / 0.18))
    if score >= 0.66:
        return "high", round(score, 3)
    if score >= 0.4:
        return "medium", round(score, 3)
    return "low", round(score, 3)


def _genre_and_style(content_type: str, tempo: str, emotion: str) -> tuple[str, str]:
    talk = {"podcast", "interview", "blogger", "youtube"}
    if content_type in talk:
        genre = "talk"
        style = "casual"
    elif content_type in {"audiobook"}:
        genre = "narrative"
        style = "formal"
    elif content_type in {"anime", "cartoon"}:
        genre = "animation"
        style = "dramatic" if emotion == "energetic" else "casual"
    else:  # movie / tv_series / default
        genre = "narrative"
        style = "dramatic" if emotion in {"energetic", "somber"} else "neutral"
    if tempo == "fast" and style == "neutral":
        style = "casual"
    return genre, style


def analyze_project(
    *,
    source_segments: Sequence[str] | None,
    translated_segments: Sequence[str] | None = None,
    timing_map: Sequence[Any] | None = None,
    src_lang: str = "",
    tgt_lang: str = "",
    content_mode_hint: str | None = None,
) -> ProjectProfile:
    """Build the project-wide :class:`ProjectProfile` from cheap signals.

    ``content_mode_hint`` (if the user picked one) is honoured; otherwise the
    content type is auto-detected from timing statistics.
    """
    src = [str(s or "") for s in (source_segments or [])]
    tgt = [str(s or "") for s in (translated_segments or [])]
    profile = ProjectProfile(source_lang=src_lang, target_lang=tgt_lang)
    profile.segment_count = len(src) or len(tgt)

    # ── Timing statistics ────────────────────────────────────────────────
    spans: list[tuple[int, int]] = []
    for row in timing_map or []:
        b = _slot_bounds_ms(row)
        if b:
            spans.append(b)
    speech_ms = sum(e - s for s, e in spans)
    if spans:
        total_ms = max(e for _, e in spans) - min(s for s, _ in spans)
        profile.total_duration_ms = max(0, total_ms)
        profile.avg_segment_ms = round(speech_ms / len(spans), 1)
        gaps = []
        ordered = sorted(spans)
        for (s0, e0), (s1, _e1) in zip(ordered, ordered[1:]):
            gaps.append(max(0, s1 - e0))
        profile.avg_gap_ms = round(sum(gaps) / len(gaps), 1) if gaps else 0.0
        if profile.total_duration_ms > 0:
            profile.dialogue_density = round(
                min(1.0, speech_ms / profile.total_duration_ms), 3
            )

    # ── Speaking tempo (words per second of speech) ──────────────────────
    total_words = sum(_word_count(t) for t in (src or tgt))
    if speech_ms > 0 and total_words > 0:
        profile.words_per_second = round(total_words / (speech_ms / 1000.0), 2)
    wps = profile.words_per_second
    if wps and wps < _TEMPO_SLOW:
        profile.tempo = "slow"
    elif wps and wps > _TEMPO_FAST:
        profile.tempo = "fast"
    else:
        profile.tempo = "medium"

    # ── Content type ─────────────────────────────────────────────────────
    if content_mode_hint:
        profile.content_type = str(content_mode_hint).strip().lower()
        profile.notes.append("content_type: user-selected")
    else:
        try:
            from engines.dubbing_engine.content_mode import auto_detect_mode

            mode = auto_detect_mode(
                video_duration_ms=profile.total_duration_ms,
                segment_count=profile.segment_count,
                avg_segment_ms=profile.avg_segment_ms,
                avg_gap_ms=profile.avg_gap_ms,
                source_hints=src[:50],
            )
            profile.content_type = getattr(mode, "value", str(mode))
            profile.notes.append("content_type: auto-detected")
        except Exception:
            profile.content_type = "movie"
            profile.notes.append("content_type: default (detect failed)")

    # ── Emotion / complexity / genre / style ─────────────────────────────
    profile.dominant_emotion = _detect_emotion(src or tgt)
    profile.complexity, profile.complexity_score = _detect_complexity(src or tgt)
    profile.genre, profile.speech_style = _genre_and_style(
        profile.content_type, profile.tempo, profile.dominant_emotion
    )

    # ── Speaker count (best-effort from timing gaps) ─────────────────────
    # Without diarization we can only estimate: many medium gaps in a talk
    # format usually implies ≥2 speakers (dialogue), otherwise 1 (narration).
    if profile.content_type in {"interview", "podcast"}:
        profile.speaker_count = 2
    elif profile.avg_gap_ms > 800 and profile.segment_count > 10:
        profile.speaker_count = 2
    else:
        profile.speaker_count = 1

    return profile
