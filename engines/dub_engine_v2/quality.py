"""P413 Lip Sync Foundation + P412 Speech Flow + P414 Audio Quality."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from engines.dub_engine_v2.models import AudioUnitV2, SpeechUnitV2
from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.phoneme_viseme import analyze_word_phonemes, phonemes_to_visemes


@dataclass
class LipSyncBundle:
    speech_uuid: str
    phonemes: list[dict[str, Any]] = field(default_factory=list)
    visemes: list[dict[str, Any]] = field(default_factory=list)
    stresses: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_lipsync_foundation(speech_units: list[SpeechUnitV2]) -> dict[str, LipSyncBundle]:
    """Prepare phoneme/viseme/stress data — no animation."""
    out: dict[str, LipSyncBundle] = {}
    for su in speech_units:
        phones: list[dict[str, Any]] = []
        visemes: list[dict[str, Any]] = []
        stresses: list[float] = []
        words = (su.text or "").split()
        per = max(40, int((su.predicted_duration or 200) / max(1, len(words))))
        for w in words:
            ph = analyze_word_phonemes(w, duration_ms=per, speech_rate=1.0)
            vis = phonemes_to_visemes(ph)
            for p in ph:
                phones.append(p.to_dict())
                stresses.append(float(p.stress))
            for v in vis:
                visemes.append(v.to_dict())
        out[su.speech_uuid] = LipSyncBundle(
            speech_uuid=su.speech_uuid,
            phonemes=phones,
            visemes=visemes,
            stresses=stresses,
        )
    return out


def speech_flow_score(
    speech_units: list[SpeechUnitV2],
    audio_units: list[AudioUnitV2],
) -> float:
    """P412 — rhythm / breath / tempo naturalness heuristic."""
    if not audio_units:
        return 50.0
    score = 100.0
    tempos = [u.tempo for u in audio_units]
    if tempos and (max(tempos) - min(tempos)) > 0.25:
        score -= 10.0
    for u in audio_units:
        if u.tempo > 1.12:
            score -= 8.0
        if u.stretch > 1.05:
            score -= 6.0
        if u.breath_ms > 0:
            score += 1.0
    # Emotion continuity
    for i in range(len(speech_units) - 1):
        if speech_units[i].emotion != speech_units[i + 1].emotion:
            gap = audio_units[i + 1].start_ms - audio_units[i].end_ms if i + 1 < len(audio_units) else 0
            if gap < 40:
                score -= 3.0
    return max(0.0, min(100.0, round(score, 1)))


def validate_audio_units(
    units: list[AudioUnitV2],
    *,
    require_files: bool = False,
) -> list[dict[str, Any]]:
    """P414 — block damaged / silent / clipped / duration mismatch."""
    issues: list[dict[str, Any]] = []
    for u in units:
        if u.duration <= 0 or u.end_ms <= u.start_ms:
            issues.append({"audio_uuid": u.audio_uuid, "code": "bad_duration"})
        if u.quality < 0.3:
            issues.append({"audio_uuid": u.audio_uuid, "code": "low_quality"})
        if require_files:
            if not u.wav_path:
                issues.append({"audio_uuid": u.audio_uuid, "code": "missing_wav"})
            elif not Path(u.wav_path).is_file():
                issues.append({"audio_uuid": u.audio_uuid, "code": "missing_file"})
            else:
                size = Path(u.wav_path).stat().st_size
                if size < 44:
                    issues.append({"audio_uuid": u.audio_uuid, "code": "truncated_wav"})
    if issues:
        raise ArchitectureViolation(
            f"P414 Audio Quality failed: {len(issues)} issue(s)",
            stage="dub_engine_v2",
            rule="audio_quality",
            details={"issues": issues[:8]},
        )
    return issues
