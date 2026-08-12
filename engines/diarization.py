"""Speaker diarization (spec v3).

Uses PyAnnote 3.x when installed and ``HF_TOKEN`` env is set; falls back to a
single-speaker assignment so downstream stages never break.

Public API:
    ``run_diarization(audio_path, *, hf_token=None) -> DiarizationResult``
    ``assign_speakers_to_segments(segments, dia_result) -> segments``
    ``extract_speaker_reference_clip(audio_path, dia_result, speaker_id, out_wav, *, min_ms=5000)``

Every function is safe to call regardless of PyAnnote availability. Diagnostics
are returned via ``DiarizationResult.to_dict()`` for OpenDDF lineage.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.diarization")


@dataclass
class SpeakerTurn:
    start_ms: int
    end_ms: int
    speaker: str

    def duration_ms(self) -> int:
        return max(0, int(self.end_ms) - int(self.start_ms))


@dataclass
class DiarizationResult:
    enabled: bool = False
    attempted: bool = False
    success: bool = False
    method: str = "none"
    turns: list[SpeakerTurn] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)
    speaker_durations_ms: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["turns"] = [asdict(t) for t in self.turns]
        return d


def is_diarization_enabled(info: dict[str, Any] | None = None) -> bool:
    """Opt-in via spec_v3 flag or env VM_DIARIZE=1."""
    if info and (info.get("spec_v3") or info.get("diarize") or info.get("diarization")):
        return True
    return (os.getenv("VM_DIARIZE") or "").strip() in ("1", "true", "yes", "on")


def _hf_token() -> str | None:
    tok = (os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN") or "").strip()
    return tok or None


def _try_pyannote(audio_path: str, *, hf_token: str | None) -> DiarizationResult | None:
    try:
        from pyannote.audio import Pipeline as _Pipeline
    except Exception as exc:
        logger.info("[diarization] pyannote not available: %s", exc)
        return None

    token = hf_token or _hf_token()
    if not token:
        logger.info("[diarization] HF_TOKEN missing — cannot load pyannote pipeline")
        return DiarizationResult(
            enabled=True,
            attempted=True,
            success=False,
            method="pyannote_missing_token",
            error="hf_token_missing",
            warning="Set HF_TOKEN env variable to enable PyAnnote diarization.",
        )

    started = time.perf_counter()
    try:
        pipeline = _Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=token,
        )
    except Exception as exc:
        logger.warning("[diarization] pyannote load failed: %s", exc)
        return DiarizationResult(
            enabled=True,
            attempted=True,
            success=False,
            method="pyannote_load_failed",
            error=str(exc)[:200],
        )

    try:
        annotation = pipeline(audio_path)
    except Exception as exc:
        logger.warning("[diarization] pyannote inference failed: %s", exc)
        return DiarizationResult(
            enabled=True,
            attempted=True,
            success=False,
            method="pyannote_inference_failed",
            error=str(exc)[:200],
        )

    turns: list[SpeakerTurn] = []
    for segment, _, label in annotation.itertracks(yield_label=True):
        turns.append(
            SpeakerTurn(
                start_ms=int(segment.start * 1000),
                end_ms=int(segment.end * 1000),
                speaker=str(label),
            )
        )
    speakers = sorted({t.speaker for t in turns})
    durations = {sp: sum(t.duration_ms() for t in turns if t.speaker == sp) for sp in speakers}
    dur_total = int((time.perf_counter() - started) * 1000)
    logger.info(
        "[diarization] pyannote ok speakers=%s turns=%s duration_ms=%s",
        len(speakers),
        len(turns),
        dur_total,
    )
    return DiarizationResult(
        enabled=True,
        attempted=True,
        success=True,
        method="pyannote/speaker-diarization-3.1",
        turns=turns,
        speakers=speakers,
        speaker_durations_ms=durations,
        duration_ms=dur_total,
    )


def _single_speaker_fallback(audio_path: str) -> DiarizationResult:
    """When PyAnnote is unavailable, treat the whole track as SPEAKER_00."""
    try:
        from pydub import AudioSegment

        seg = AudioSegment.from_file(audio_path)
        total_ms = int(len(seg))
    except Exception:
        total_ms = 0
    turn = SpeakerTurn(start_ms=0, end_ms=total_ms or 60_000, speaker="SPEAKER_00")
    return DiarizationResult(
        enabled=True,
        attempted=True,
        success=True,
        method="single_speaker_fallback",
        turns=[turn],
        speakers=["SPEAKER_00"],
        speaker_durations_ms={"SPEAKER_00": turn.duration_ms()},
        duration_ms=0,
        warning="pyannote unavailable; using single-speaker fallback (no per-voice profiles)",
    )


def run_diarization(
    audio_path: str,
    *,
    hf_token: str | None = None,
    task_info: dict[str, Any] | None = None,
) -> DiarizationResult:
    """Attempt PyAnnote diarization; fall back to single-speaker.

    Never raises — downstream stages must always receive a usable result.
    """
    if not is_diarization_enabled(task_info):
        return DiarizationResult(
            enabled=False,
            attempted=False,
            method="disabled",
            warning="Diarization disabled (spec_v3/VM_DIARIZE opt-in)",
        )

    if not Path(audio_path).is_file():
        return DiarizationResult(
            enabled=True,
            attempted=True,
            method="input_missing",
            error=f"audio not found: {audio_path}",
        )

    pyres = _try_pyannote(audio_path, hf_token=hf_token)
    if pyres is not None and pyres.success:
        return pyres
    fallback = _single_speaker_fallback(audio_path)
    if pyres is not None and not pyres.success:
        fallback.warning = (
            fallback.warning or ""
        ) + f" | pyannote_error: {pyres.error or pyres.method}"
    return fallback


def _overlap_ms(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def assign_speakers_to_segments(
    segments: list[dict[str, Any]],
    dia: DiarizationResult,
) -> list[dict[str, Any]]:
    """Stamp seg["speaker"] and seg["speaker_confidence"] using overlap-max heuristic.

    Mutates ``segments`` in place and also returns it for chaining.
    """
    if not segments or not dia or not dia.turns:
        for s in segments or []:
            s.setdefault("speaker", "SPEAKER_00")
            s.setdefault("speaker_confidence", 0.0)
        return segments or []

    for seg in segments:
        try:
            s0 = int(seg.get("start_ms") or 0)
            s1 = int(seg.get("end_ms") or (s0 + int(seg.get("duration_ms") or 0)))
        except Exception:
            s0, s1 = 0, 0
        if s1 <= s0:
            seg.setdefault("speaker", "SPEAKER_00")
            seg.setdefault("speaker_confidence", 0.0)
            continue
        per_speaker: dict[str, int] = {}
        for t in dia.turns:
            ov = _overlap_ms(s0, s1, t.start_ms, t.end_ms)
            if ov > 0:
                per_speaker[t.speaker] = per_speaker.get(t.speaker, 0) + ov
        if per_speaker:
            best = max(per_speaker.items(), key=lambda kv: kv[1])
            total_ov = sum(per_speaker.values())
            seg["speaker"] = best[0]
            seg["speaker_confidence"] = round(best[1] / max(1, total_ov), 4)
        else:
            seg["speaker"] = "SPEAKER_00"
            seg["speaker_confidence"] = 0.0
    return segments


def _ffmpeg_bin() -> str | None:
    try:
        from engines.ffmpeg_paths import find_ffmpeg

        return find_ffmpeg()
    except Exception:
        return None


def extract_speaker_reference_clip(
    audio_path: str,
    dia: DiarizationResult,
    speaker: str,
    out_wav: str,
    *,
    min_ms: int = 5000,
    max_ms: int = 12000,
) -> dict[str, Any]:
    """Concat the longest turns of ``speaker`` into a single mono 16k WAV.

    Returns diagnostics dict with ``ok``, ``file``, ``duration_ms``, ``turns_used``.
    """
    ffmpeg = _ffmpeg_bin()
    diag: dict[str, Any] = {"speaker": speaker, "ok": False, "file": None, "duration_ms": 0}
    if not ffmpeg or not Path(audio_path).is_file():
        diag["error"] = "ffmpeg_or_audio_missing"
        return diag
    turns = sorted(
        [t for t in dia.turns if t.speaker == speaker and t.duration_ms() > 300],
        key=lambda t: t.duration_ms(),
        reverse=True,
    )
    if not turns:
        diag["error"] = "no_turns"
        return diag

    picked: list[SpeakerTurn] = []
    total = 0
    for t in turns:
        picked.append(t)
        total += t.duration_ms()
        if total >= min_ms:
            break
    if not picked:
        diag["error"] = "empty_pick"
        return diag

    parts_dir = Path(out_wav).parent / "_spk_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[str] = []
    for i, t in enumerate(picked):
        p = parts_dir / f"{Path(out_wav).stem}_{i:02d}.wav"
        duration = min(max_ms, t.duration_ms()) / 1000.0
        try:
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{t.start_ms / 1000.0:.3f}",
                    "-t", f"{duration:.3f}",
                    "-i", audio_path,
                    "-ac", "1", "-ar", "16000",
                    "-acodec", "pcm_s16le",
                    str(p),
                ],
                capture_output=True, text=True, timeout=120,
            )
            if p.is_file() and p.stat().st_size > 0:
                part_paths.append(str(p))
        except Exception as exc:
            logger.debug("[diarization] part cut failed: %s", exc)
    if not part_paths:
        diag["error"] = "cut_failed"
        return diag

    if len(part_paths) == 1:
        try:
            import shutil as _sh

            _sh.copy2(part_paths[0], out_wav)
        except Exception as exc:
            diag["error"] = f"copy_failed: {exc}"
            return diag
    else:
        concat_list = parts_dir / f"{Path(out_wav).stem}_list.txt"
        concat_list.write_text(
            "\n".join(f"file '{p}'" for p in part_paths), encoding="utf-8"
        )
        try:
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_list),
                    "-ac", "1", "-ar", "16000",
                    "-acodec", "pcm_s16le",
                    out_wav,
                ],
                capture_output=True, text=True, timeout=120,
            )
        except Exception as exc:
            diag["error"] = f"concat_failed: {exc}"
            return diag

    if Path(out_wav).is_file():
        try:
            from pydub import AudioSegment as _AS

            diag["duration_ms"] = int(len(_AS.from_file(out_wav)))
        except Exception:
            diag["duration_ms"] = int(total)
        diag["ok"] = True
        diag["file"] = str(Path(out_wav).resolve())
        diag["turns_used"] = len(part_paths)
    else:
        diag["error"] = "out_missing"
    return diag


def build_speaker_profiles(
    audio_path: str,
    dia: DiarizationResult,
    profiles_dir: str,
    *,
    min_ms: int = 5000,
    max_ms: int = 12000,
) -> dict[str, dict[str, Any]]:
    """Extract a reference clip for each detected speaker.

    Returns ``{speaker_id: {ok, file, duration_ms, ...}}``.
    """
    profiles_dir_path = Path(profiles_dir)
    profiles_dir_path.mkdir(parents=True, exist_ok=True)
    profiles: dict[str, dict[str, Any]] = {}
    if not dia or not dia.success or not dia.speakers:
        return profiles
    for sp in dia.speakers:
        out_wav = profiles_dir_path / f"speaker_{sp}.wav"
        profiles[sp] = extract_speaker_reference_clip(
            audio_path, dia, sp, str(out_wav), min_ms=min_ms, max_ms=max_ms
        )
    return profiles
