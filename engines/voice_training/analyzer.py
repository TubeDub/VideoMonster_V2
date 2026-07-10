"""Voice Training — speech coach (TZ Etap 6)."""

from __future__ import annotations

import re
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.platform_diagnostics.sink import PlatformTraceSink


@dataclass
class VoiceMetrics:
    words_per_minute: float = 0.0
    pause_count: int = 0
    avg_pause_ms: float = 0.0
    duration_sec: float = 0.0
    script_match_ratio: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class VoiceTrainingResult:
    metrics: VoiceMetrics
    recommendations: list[str]
    transcript: str = ""


def _normalize_words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[\w'-]+", text, flags=re.UNICODE) if w]


def analyze_voice_recording(
    wav_path: str,
    *,
    script: str = "",
    app_dir: Path | None = None,
    session_id: str = "analysis",
) -> VoiceTrainingResult:
    """Analyze diction, tempo, pauses; optional compare to script via STT."""
    path = Path(wav_path)
    sink = PlatformTraceSink(
        Path(app_dir or Path(__file__).resolve().parents[2]),
        module="voice_training",
        session_id=session_id,
    )

    duration = 0.0
    try:
        with wave.open(str(path), "rb") as w:
            duration = w.getnframes() / float(w.getframerate())
    except Exception:
        pass

    transcript = ""
    try:
        from engines.stt_engine import transcribe

        transcript, _, _, _ = transcribe(str(path), model_size="tiny", word_timestamps=False)
        transcript = " ".join(str(transcript or "").split())
    except Exception as e:
        sink.log(stage="voice_training.stt.error", error=str(e))

    words = _normalize_words(transcript)
    wpm = (len(words) / duration * 60.0) if duration > 0.5 else 0.0

    script_words = _normalize_words(script)
    match_ratio = 0.0
    if script_words and words:
        matched = sum(1 for w in words if w in script_words)
        match_ratio = matched / max(len(script_words), 1)

    pause_count = max(0, transcript.count("...") + transcript.count("—"))
    recommendations: list[str] = []

    if wpm > 180:
        recommendations.append("Темп слишком быстрый — замедлите речь на 10–15%.")
    elif 0 < wpm < 90:
        recommendations.append("Темп медленный — добавьте динамики, не растягивайте паузы.")
    if script and match_ratio < 0.6:
        recommendations.append("Произношение расходится с текстом — прочитайте скрипт ещё раз, чётче артикулируя.")
    if pause_count == 0 and duration > 10:
        recommendations.append("Добавьте короткие паузы между смысловыми блоками.")
    if not recommendations:
        recommendations.append("Хороший базовый уровень — попробуйте следующий фрагмент с большей выразительностью.")

    metrics = VoiceMetrics(
        words_per_minute=round(wpm, 1),
        pause_count=pause_count,
        avg_pause_ms=0.0,
        duration_sec=round(duration, 2),
        script_match_ratio=round(match_ratio, 3),
    )

    sink.log(
        stage="voice_training.analyze",
        input_preview=script[:200],
        output_preview=transcript[:200],
        quality_score=match_ratio * 100 if script else wpm,
        meta=metrics.extra | {"wpm": wpm, "match": match_ratio},
    )

    return VoiceTrainingResult(
        metrics=metrics,
        recommendations=recommendations,
        transcript=transcript,
    )
