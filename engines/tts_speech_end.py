"""TTS end-of-speech check — detect truncated / abrupty cut voice tails.

Used by post-TTS QA and Translation Review. Does not rewrite TTS synthesis.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def analyze_speech_end(
    wav_path: str | Path | None,
    *,
    slot_ms: int = 0,
    playback_ms: int = 0,
    tail_ms: int = 80,
    silence_dbfs: float = -38.0,
) -> dict[str, Any]:
    """Return speech-end diagnostics for a synthesized segment WAV.

    Heuristic: if the last ``tail_ms`` of audio is still loud (not near silence)
    AND playback overshoots the slot, the voice was likely hard-cut.
    """
    result: dict[str, Any] = {
        "voice_finished_naturally": True,
        "voice_truncated": False,
        "tail_dbfs": None,
        "playback_ms": int(playback_ms or 0),
        "slot_ms": int(slot_ms or 0),
        "reason": "",
    }
    path = Path(str(wav_path)) if wav_path else None
    if not path or not path.is_file():
        # Duration-only fallback
        if slot_ms > 0 and playback_ms > slot_ms + 120:
            result["voice_truncated"] = True
            result["voice_finished_naturally"] = False
            result["reason"] = "duration_overflow_no_wav"
        return result

    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(str(path))
        dur = int(len(audio))
        result["playback_ms"] = dur if dur > 0 else int(playback_ms or 0)
        if dur <= 0:
            return result
        take = min(tail_ms, dur)
        tail = audio[-take:]
        dbfs = float(tail.dBFS) if tail.dBFS != float("-inf") else -90.0
        result["tail_dbfs"] = round(dbfs, 1)
        overshoot = (slot_ms > 0) and (dur > slot_ms + 80)
        loud_tail = dbfs > silence_dbfs
        # Natural endings usually fade / have trailing silence
        if overshoot and loud_tail:
            result["voice_truncated"] = True
            result["voice_finished_naturally"] = False
            result["reason"] = "loud_tail_past_slot"
        elif overshoot and dur > slot_ms + 250:
            result["voice_truncated"] = True
            result["voice_finished_naturally"] = False
            result["reason"] = "hard_overshoot"
        else:
            result["reason"] = "ok"
    except Exception as exc:
        result["reason"] = f"analyze_error:{exc}"
        if slot_ms > 0 and playback_ms > slot_ms + 150:
            result["voice_truncated"] = True
            result["voice_finished_naturally"] = False
    return result


def apply_speech_end_to_segment(
    seg: dict[str, Any],
    *,
    wav_path: str | Path | None = None,
    slot_ms: int | None = None,
) -> dict[str, Any]:
    """Mutate segment with voice_truncated / voice_finished_naturally flags."""
    slot = int(
        slot_ms
        if slot_ms is not None
        else (seg.get("slot_ms") or seg.get("timing_slot_ms") or 0)
    )
    playback = int(seg.get("playback_duration") or seg.get("tts_ms") or 0)
    path = wav_path or seg.get("tts_file_path") or seg.get("file")
    info = analyze_speech_end(path, slot_ms=slot, playback_ms=playback)
    seg["voice_truncated"] = bool(info.get("voice_truncated"))
    seg["voice_finished_naturally"] = bool(info.get("voice_finished_naturally"))
    seg["tts_speech_end"] = info
    if info.get("voice_truncated"):
        retry = seg.setdefault("post_tts_retry", {"attempts": 0, "reasons": []})
        retry["truncated"] = True
        reasons = retry.setdefault("reasons", [])
        reason = str(info.get("reason") or "voice_truncated")
        if reason not in reasons:
            reasons.append(reason)
    return info
