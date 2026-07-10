"""Vocal Training — karaoke coach (TZ Etap 7)."""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.platform_diagnostics.sink import PlatformTraceSink


@dataclass
class VocalMetrics:
    pitch_mean_hz: float = 0.0
    pitch_stability: float = 0.0
    duration_sec: float = 0.0
    note_accuracy_cents: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class VocalTrainingResult:
    metrics: VocalMetrics
    recommendations: list[str]
    score: float = 0.0


def _estimate_pitch_simple(samples, rate: int) -> float:
    """Zero-crossing pitch estimate (fallback without librosa)."""
    if len(samples) < rate // 10:
        return 0.0
    crossings = 0
    for i in range(1, len(samples)):
        if (samples[i - 1] >= 0) != (samples[i] >= 0):
            crossings += 1
    if crossings < 4:
        return 0.0
    freq = crossings / 2.0 / (len(samples) / rate)
    if 80 <= freq <= 800:
        return freq
    return 0.0


def analyze_vocal_recording(
    wav_path: str,
    *,
    target_note_hz: float | None = None,
    app_dir: Path | None = None,
    session_id: str = "vocal",
) -> VocalTrainingResult:
    path = Path(wav_path)
    sink = PlatformTraceSink(
        Path(app_dir or Path(__file__).resolve().parents[2]),
        module="vocal_training",
        session_id=session_id,
    )

    duration = 0.0
    pitch_hz = 0.0
    stability = 0.0
    engine = "zero-crossing"

    try:
        import numpy as np  # type: ignore

        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            frames = w.readframes(w.getnframes())
            duration = w.getnframes() / float(rate)
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
        if data.size == 0:
            raise ValueError("empty audio")

        try:
            import librosa  # type: ignore

            f0, _, _ = librosa.pyin(
                data / 32768.0,
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C7"),
                sr=rate,
            )
            voiced = f0[~np.isnan(f0)]
            if voiced.size:
                pitch_hz = float(np.nanmean(voiced))
                stability = float(1.0 - min(1.0, np.nanstd(voiced) / max(pitch_hz, 1.0)))
                engine = "librosa-pyin"
        except Exception:
            chunk = data[: rate * 2]
            pitch_hz = _estimate_pitch_simple(chunk, rate)
            stability = 0.5
    except Exception as e:
        sink.log(stage="vocal_training.error", error=str(e))

    cents_off: float | None = None
    if target_note_hz and pitch_hz > 0:
        cents_off = 1200 * math.log2(pitch_hz / target_note_hz)

    recommendations: list[str] = []
    score = 70.0
    if cents_off is not None:
        if abs(cents_off) <= 25:
            recommendations.append("Отличное попадание в ноту.")
            score = 95.0
        elif abs(cents_off) <= 50:
            recommendations.append("Близко к ноте — слегка подстройте высоту.")
            score = 80.0
        else:
            direction = "выше" if cents_off > 0 else "ниже"
            recommendations.append(f"Сместитесь {direction} примерно на {abs(cents_off):.0f} cents.")
            score = 55.0
    else:
        recommendations.append("Спойте фрагмент ещё раз — удерживайте стабильную ноту.")

    if stability < 0.4 and pitch_hz > 0:
        recommendations.append("Работайте над стабильностью голоса — избегайте «плавания» pitch.")

    metrics = VocalMetrics(
        pitch_mean_hz=round(pitch_hz, 2),
        pitch_stability=round(stability, 3),
        duration_sec=round(duration, 2),
        note_accuracy_cents=round(cents_off, 1) if cents_off is not None else None,
    )

    sink.log(
        stage="vocal_training.analyze",
        input_preview=str(path.name),
        output_preview=f"pitch={pitch_hz:.1f}Hz",
        engine=engine,
        quality_score=score,
    )

    return VocalTrainingResult(
        metrics=metrics,
        recommendations=recommendations,
        score=score,
    )
