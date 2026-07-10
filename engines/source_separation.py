"""
Source separation for dubbing — isolated pre-STT / post-TTS integration.

Splits extracted audio into:
  - Dialogue (for Whisper / STT only)
  - Music + SFX (preserved for final mix, not used in STT/TTS pipeline)

Falls back to the legacy full-mix path when separation fails.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.source_separation")

FEATURE_ID = "source_separation"
DEFAULT_ACCOMPANIMENT_ATTENUATION_DB = 4.5  # ~3–6 dB below unity


@dataclass
class SeparationResult:
    enabled: bool = False
    attempted: bool = False
    success: bool = False
    fallback_used: bool = True
    method: str = "none"
    dialogue_path: str | None = None
    dialogue_stt_path: str | None = None
    accompaniment_path: str | None = None
    stereo_source_path: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    accompaniment_attenuation_db: float = DEFAULT_ACCOMPANIMENT_ATTENUATION_DB
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinalMixDiagnostics:
    attempted: bool = False
    success: bool = False
    used_stem_mix: bool = False
    fallback_used: bool = True
    dialogue_path: str | None = None
    accompaniment_path: str | None = None
    final_mp4_path: str | None = None
    music_detected_in_final: bool = False
    accompaniment_attenuation_db: float = DEFAULT_ACCOMPANIMENT_ATTENUATION_DB
    error: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_source_separation_enabled() -> bool:
    try:
        from engines.core.feature_flags import is_enabled

        return is_enabled(FEATURE_ID, developer_session=True)
    except Exception:
        return True


def _ffmpeg_bin() -> str | None:
    from engines.ffmpeg_paths import find_ffmpeg

    return find_ffmpeg()


def _ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def _run_ffmpeg(args: list[str], *, timeout: int = 600) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip()[-500:]
            return False, tail or f"ffmpeg exit {res.returncode}"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _probe_audio_channels(path: str) -> int:
    probe = _ffprobe_bin()
    if not probe or not Path(path).is_file():
        return 0
    try:
        res = subprocess.run(
            [
                probe,
                "-v",
                "quiet",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=channels",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode == 0 and res.stdout.strip().isdigit():
            return int(res.stdout.strip())
    except Exception:
        pass
    return 0


def _probe_duration_ms(path: str) -> int | None:
    probe = _ffprobe_bin()
    if not probe or not Path(path).is_file():
        return None
    try:
        res = subprocess.run(
            [
                probe,
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode == 0 and res.stdout.strip():
            return int(float(res.stdout.strip()) * 1000)
    except Exception:
        pass
    return None


def _extract_stereo_wav(
    video_path: str,
    output_wav: str,
    ffmpeg: str,
) -> bool:
    ok, _ = _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            output_wav,
        ],
        timeout=600,
    )
    return ok and Path(output_wav).is_file()


def _convert_to_stt_mono_mp3(
    input_path: str,
    output_mp3: str,
    ffmpeg: str,
) -> bool:
    ok, _ = _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            input_path,
            "-acodec",
            "mp3",
            "-ar",
            "16000",
            "-ac",
            "1",
            output_mp3,
        ],
        timeout=300,
    )
    return ok and Path(output_mp3).is_file()


def _convert_to_mono_wav(
    input_path: str,
    output_wav: str,
    ffmpeg: str,
) -> bool:
    ok, _ = _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            input_path,
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "1",
            output_wav,
        ],
        timeout=300,
    )
    return ok and Path(output_wav).is_file()


def _try_demucs(
    stereo_path: str,
    dialogue_wav: str,
    accompaniment_wav: str,
) -> bool:
    demucs_cli = shutil.which("demucs")
    if not demucs_cli:
        return False

    out_root = Path(dialogue_wav).parent / "_demucs_out"
    out_root.mkdir(parents=True, exist_ok=True)
    try:
        cmd = [
            demucs_cli,
            "-n",
            "htdemucs",
            "--two-stems",
            "vocals",
            "-o",
            str(out_root),
            stereo_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if res.returncode != 0:
            return False

        stem_dir = out_root / "htdemucs" / Path(stereo_path).stem
        vocals = stem_dir / "vocals.wav"
        no_vocals = stem_dir / "no_vocals.wav"
        if not vocals.is_file() or not no_vocals.is_file():
            return False

        shutil.copy2(vocals, dialogue_wav)
        shutil.copy2(no_vocals, accompaniment_wav)
        return Path(dialogue_wav).is_file() and Path(accompaniment_wav).is_file()
    except Exception as exc:
        logger.debug("demucs separation failed: %s", exc)
        return False


def _try_ffmpeg_center_side(
    stereo_path: str,
    dialogue_wav: str,
    accompaniment_wav: str,
    ffmpeg: str,
) -> bool:
    """Center-channel vocals + side-emphasis accompaniment (karaoke-style approximation)."""
    ok_dialogue, err_d = _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            stereo_path,
            "-af",
            "pan=mono|c0=0.5*FL+0.5*FR",
            "-acodec",
            "pcm_s16le",
            dialogue_wav,
        ],
        timeout=600,
    )
    ok_accomp, err_a = _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            stereo_path,
            "-af",
            "pan=mono|c0=0.5*FL-0.5*FR",
            "-acodec",
            "pcm_s16le",
            accompaniment_wav,
        ],
        timeout=600,
    )
    if not ok_dialogue or not ok_accomp:
        logger.debug(
            "ffmpeg center/side failed dialogue=%s accomp=%s",
            err_d[:200] if err_d else "",
            err_a[:200] if err_a else "",
        )
        return False
    return Path(dialogue_wav).is_file() and Path(accompaniment_wav).is_file()


def _fallback_result(
    *,
    enabled: bool,
    attempted: bool,
    mono_audio_path: str,
    warning: str,
    error: str | None = None,
    duration_ms: int | None = None,
) -> SeparationResult:
    return SeparationResult(
        enabled=enabled,
        attempted=attempted,
        success=False,
        fallback_used=True,
        method="fallback",
        dialogue_path=None,
        dialogue_stt_path=mono_audio_path,
        accompaniment_path=None,
        stereo_source_path=None,
        error=error,
        duration_ms=duration_ms,
        accompaniment_attenuation_db=DEFAULT_ACCOMPANIMENT_ATTENUATION_DB,
        warning=warning,
    )


def try_separate_audio(
    *,
    video_path: str,
    mono_audio_path: str,
    artifacts_dir: Path,
    base_id: str,
    task_id: str = "",
) -> SeparationResult:
    """
    Attempt dialogue / music+SFX separation. On failure returns fallback (STT uses full mix).
    """
    duration_ms = _probe_duration_ms(mono_audio_path)
    enabled = is_source_separation_enabled()
    if not enabled:
        return _fallback_result(
            enabled=False,
            attempted=False,
            mono_audio_path=mono_audio_path,
            warning="Source separation disabled by feature flag; legacy mix path.",
            duration_ms=duration_ms,
        )

    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return _fallback_result(
            enabled=True,
            attempted=True,
            mono_audio_path=mono_audio_path,
            warning="FFmpeg not found; source separation skipped.",
            error="ffmpeg_missing",
            duration_ms=duration_ms,
        )

    artifacts_dir = Path(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    stereo_path = str(artifacts_dir / f"{base_id}_stereo_src.wav")
    dialogue_wav = str(artifacts_dir / f"{base_id}_dialogue.wav")
    accompaniment_wav = str(artifacts_dir / f"{base_id}_music_sfx.wav")
    dialogue_stt_mp3 = str(artifacts_dir / f"{base_id}_dialogue_stt.mp3")

    started = time.perf_counter()
    attempted = True

    if not _extract_stereo_wav(video_path, stereo_path, ffmpeg):
        return _fallback_result(
            enabled=True,
            attempted=attempted,
            mono_audio_path=mono_audio_path,
            warning="Stereo extract failed; using legacy full-mix STT path.",
            error="stereo_extract_failed",
            duration_ms=duration_ms,
        )

    channels = _probe_audio_channels(stereo_path)
    if channels < 2:
        return _fallback_result(
            enabled=True,
            attempted=attempted,
            mono_audio_path=mono_audio_path,
            warning="Source is mono; separation skipped (legacy path).",
            error="mono_source",
            duration_ms=duration_ms,
        )

    method = "none"
    separated = False
    if _try_demucs(stereo_path, dialogue_wav, accompaniment_wav):
        method = "demucs_htdemucs"
        separated = True
    elif _try_ffmpeg_center_side(stereo_path, dialogue_wav, accompaniment_wav, ffmpeg):
        method = "ffmpeg_center_side"
        separated = True

    if not separated:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _fallback_result(
            enabled=True,
            attempted=attempted,
            mono_audio_path=mono_audio_path,
            warning="Source separation failed; legacy full-mix assembly used.",
            error="separation_failed",
            duration_ms=duration_ms or elapsed_ms,
        )

    if not _convert_to_stt_mono_mp3(dialogue_wav, dialogue_stt_mp3, ffmpeg):
        dialogue_stt_mp3 = mono_audio_path

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Task %s: source separation ok method=%s dialogue=%s accomp=%s",
        task_id,
        method,
        dialogue_wav,
        accompaniment_wav,
    )

    return SeparationResult(
        enabled=True,
        attempted=True,
        success=True,
        fallback_used=False,
        method=method,
        dialogue_path=dialogue_wav,
        dialogue_stt_path=dialogue_stt_mp3,
        accompaniment_path=accompaniment_wav,
        stereo_source_path=stereo_path,
        error=None,
        duration_ms=duration_ms or elapsed_ms,
        accompaniment_attenuation_db=DEFAULT_ACCOMPANIMENT_ATTENUATION_DB,
        warning=None,
    )


def get_accompaniment_path_from_task_info(info: dict[str, Any]) -> str | None:
    sep = info.get("source_separation") or {}
    if not sep.get("success"):
        return None
    path = sep.get("accompaniment_path")
    if path and Path(str(path)).is_file():
        return str(path)
    return None


def get_background_mix_params(info: dict[str, Any]) -> tuple[str | None, float, bool]:
    """Return (accompaniment_path, attenuation_db, separation_success)."""
    sep = info.get("source_separation") or {}
    if not sep.get("success"):
        return None, DEFAULT_ACCOMPANIMENT_ATTENUATION_DB, False
    path = get_accompaniment_path_from_task_info(info)
    atten = float(sep.get("accompaniment_attenuation_db") or DEFAULT_ACCOMPANIMENT_ATTENUATION_DB)
    return path, atten, path is not None


def _probe_audio_rms_db(path: str, ffmpeg: str) -> float | None:
    try:
        res = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-i",
                path,
                "-af",
                "astats=metadata=1:reset=1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        for line in (res.stderr or "").splitlines():
            if "RMS level dB" in line:
                part = line.split("RMS level dB:")[-1].strip()
                try:
                    return float(part.split()[0])
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return None


def build_final_mix_diagnostics(
    *,
    separation_info: dict[str, Any],
    final_mp4_path: str,
    mix_success: bool,
    used_stem_mix: bool,
    error: str | None = None,
) -> FinalMixDiagnostics:
    ffmpeg = _ffmpeg_bin()
    accomp_path = separation_info.get("accompaniment_path")
    dialogue_path = separation_info.get("dialogue_path")
    atten = float(
        separation_info.get("accompaniment_attenuation_db") or DEFAULT_ACCOMPANIMENT_ATTENUATION_DB
    )

    warning = None
    fallback_used = not used_stem_mix
    music_detected = False

    if separation_info.get("fallback_used"):
        warning = separation_info.get("warning") or (
            "Source separation unavailable; legacy assembly without preserved music stem."
        )

    if used_stem_mix and mix_success and Path(final_mp4_path).is_file():
        music_detected = True
        if ffmpeg and accomp_path and Path(str(accomp_path)).is_file():
            final_rms = _probe_audio_rms_db(final_mp4_path, ffmpeg)
            accomp_rms = _probe_audio_rms_db(str(accomp_path), ffmpeg)
            if final_rms is not None and accomp_rms is not None:
                # Final mix should be louder than accompaniment-only stem at same scale.
                music_detected = final_rms > accomp_rms - 6.0
    elif mix_success and not used_stem_mix:
        warning = warning or "Final mix used legacy replace path (no music stem)."

    return FinalMixDiagnostics(
        attempted=True,
        success=mix_success,
        used_stem_mix=used_stem_mix,
        fallback_used=fallback_used,
        dialogue_path=dialogue_path,
        accompaniment_path=accomp_path if separation_info.get("success") else None,
        final_mp4_path=final_mp4_path if mix_success else None,
        music_detected_in_final=music_detected,
        accompaniment_attenuation_db=atten,
        error=error,
        warning=warning,
    )


def merge_openddf_source_separation(
    task_info: dict[str, Any],
    final_mix: FinalMixDiagnostics | None = None,
) -> dict[str, Any]:
    """OpenDDF block for source separation + final mix."""
    sep = dict(task_info.get("source_separation") or {})
    block: dict[str, Any] = {
        "separation_performed": bool(sep.get("attempted")),
        "separation_success": bool(sep.get("success")),
        "fallback_used": bool(sep.get("fallback_used")),
        "method": sep.get("method"),
        "dialogue_path": sep.get("dialogue_path"),
        "accompaniment_path": sep.get("accompaniment_path"),
        "dialogue_stt_path": sep.get("dialogue_stt_path"),
        "accompaniment_attenuation_db": sep.get("accompaniment_attenuation_db"),
        "warning": sep.get("warning"),
        "error": sep.get("error"),
    }
    if final_mix is not None:
        block["final_mix"] = final_mix.to_dict()
    elif task_info.get("source_separation_final_mix"):
        block["final_mix"] = task_info.get("source_separation_final_mix")
    return block
