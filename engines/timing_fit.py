"""Fit TTS segments to timing_map slots; build gap-aware dub master track."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from pydub import AudioSegment
from pydub.silence import detect_nonsilent

logger = logging.getLogger(__name__)

# ─── Quality-first constants ──────────────────────────────────────────────────
# atempo is the LAST RESORT — listener must not hear unnatural speed.
# Priority: natural speech > stress marks > lip sync > exact duration match.
_ATEMPO_MIN = 0.92
_ATEMPO_ABSOLUTE_MAX = 1.05      # hard ceiling — was 1.18; capped at barely-noticeable
DUB_MAX_ATEMPO = 1.05            # per-segment cap  — was 1.15
DUB_SLOT_TOLERANCE_MS = 75

# Video-adaptation window: if overflow ≤ this %, prefer gap-borrow / video
# slowdown instead of touching speech at all.
VIDEO_ADAPT_MAX_OVERFLOW_PCT = 15.0

# Extra gap that can be consumed before resorting to atempo
_MAX_BORROW_MS = 3000            # was 2500; more room for natural speech

# Pause compression
_PAUSE_COMPRESS_THRESH = -38
_MIN_SILENCE_LEN = 80
_MAX_INTERNAL_PAUSE = 185
_PRESERVE_PAUSE_MS = 240

# ─── Natural post-sentence pauses ─────────────────────────────────────────────
# After each dubbed segment we add only a small punctuation-based pause rather
# than padding silence to fill the full slot.  FFmpeg adelay handles exact
# placement in the timeline, so full-slot padding is unnecessary and causes the
# "long dead air" after speech ends that the user hears.
_PUNCT_PAUSE_MS: dict[str, int] = {
    ".": 160,
    "!": 150,
    "?": 150,
    "…": 200,
    ";": 100,
    ":": 90,
    ",": 80,
}
_DEFAULT_PUNCT_PAUSE_MS = 120
_MIN_NATURAL_PAUSE_MS = 80
_MAX_NATURAL_PAUSE_MS = 220

# Significant underfill: if TTS fills < 55% of slot the silence is noticeable.
UNDERFILL_SIGNIFICANT_THRESH = 0.55


# ─── Overflow classification ──────────────────────────────────────────────────
class OverflowClass:
    """Result of classify_segment_overflow()."""
    __slots__ = ("label", "overflow_ms", "overflow_pct", "video_stretch_ratio")

    def __init__(self, label: str, overflow_ms: int, overflow_pct: float, video_stretch_ratio: float):
        self.label = label                       # "fits" | "gap_absorb" | "video_adapt" | "needs_shorten"
        self.overflow_ms = overflow_ms
        self.overflow_pct = overflow_pct
        self.video_stretch_ratio = video_stretch_ratio  # > 1.0 when video must slow


def classify_segment_overflow(
    tts_ms: int,
    slot_ms: int,
    gap_after_ms: int = 0,
    *,
    tolerance_ms: int = DUB_SLOT_TOLERANCE_MS,
) -> OverflowClass:
    """
    Classify how to handle a segment that may not fit its slot.

    Returns OverflowClass with:
      "fits"         — already within tolerance; no action needed
      "gap_absorb"   — overflow ≤ 15% AND gap large enough; borrow from gap
      "video_adapt"  — overflow ≤ 15% AND gap too small; slow video slightly
      "needs_shorten"— overflow > 15%; must shorten translation or stretch minimally
    """
    effective_slot = slot_ms + tolerance_ms
    overflow_ms = max(0, tts_ms - effective_slot)
    overflow_pct = 100.0 * overflow_ms / max(slot_ms, 1)

    if overflow_ms <= 0:
        return OverflowClass("fits", 0, 0.0, 1.0)

    video_stretch_ratio = tts_ms / max(slot_ms, 1)

    if overflow_pct <= VIDEO_ADAPT_MAX_OVERFLOW_PCT:
        if gap_after_ms >= overflow_ms:
            return OverflowClass("gap_absorb", overflow_ms, round(overflow_pct, 1), video_stretch_ratio)
        return OverflowClass("video_adapt", overflow_ms, round(overflow_pct, 1), video_stretch_ratio)

    return OverflowClass("needs_shorten", overflow_ms, round(overflow_pct, 1), video_stretch_ratio)


def cleanup_stale_work_dirs(max_age_sec: int = 3600) -> int:
    """Remove orphaned timing_fit_* temp folders (no desktop/output pollution)."""
    removed = 0
    tmp_root = Path(tempfile.gettempdir())
    cutoff = time.time() - max(60, int(max_age_sec))
    for pattern in ("timing_fit_*", "timing_fit_track_*"):
        for path in tmp_root.glob(pattern):
            if not path.is_dir():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
    if removed:
        logger.info("timing_fit: removed %d stale temp dirs", removed)
    return removed


def natural_sentence_pause_ms(text: str) -> int:
    """Return the natural post-sentence pause (80–220 ms) based on ending punctuation."""
    t = (text or "").rstrip()
    if not t:
        return _DEFAULT_PUNCT_PAUSE_MS
    return max(
        _MIN_NATURAL_PAUSE_MS,
        min(_MAX_NATURAL_PAUSE_MS, _PUNCT_PAUSE_MS.get(t[-1], _DEFAULT_PUNCT_PAUSE_MS)),
    )


def detect_significant_underfill(tts_ms: int, slot_ms: int) -> bool:
    """True when TTS is significantly shorter than slot — risk of long dead air."""
    if slot_ms <= 0:
        return False
    return tts_ms < slot_ms * UNDERFILL_SIGNIFICANT_THRESH


def _parse_timing(item: Any) -> tuple[int, int]:
    if isinstance(item, dict):
        return int(item["start"]), int(item["end"])
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[0]), int(item[1])
    return 0, 0


# ─── Loudness normalization ───────────────────────────────────────────────────
# Target RMS loudness for every TTS segment before slot-fitting and mixing.
# -18 dBFS RMS ≈ comfortable speech level; keeps consistent volume across
# segments regardless of how Edge TTS generated them.
_TARGET_SEGMENT_DBFS: float = -18.0
# Maximum gain allowed upward (don't boost near-silent segments into clipping)
_MAX_GAIN_UP_DB: float = 8.0
# Maximum attenuation (don't crush naturally loud segments too aggressively)
_MAX_GAIN_DOWN_DB: float = -12.0


def normalize_segment_loudness(
    audio: AudioSegment,
    target_dbfs: float = _TARGET_SEGMENT_DBFS,
) -> tuple[AudioSegment, float]:
    """
    Normalize a TTS segment to a consistent RMS loudness level.

    Returns (normalized_audio, applied_gain_db).
    Skips silence and clips applied gain to a safe window so natural
    dynamics are preserved — we just remove the large inter-segment jumps.
    """
    cur = audio.dBFS
    if cur == float("-inf") or len(audio) < 20:
        return audio, 0.0   # silence — leave unchanged

    gain = target_dbfs - cur
    # Clamp to safe window
    gain = max(_MAX_GAIN_DOWN_DB, min(_MAX_GAIN_UP_DB, gain))
    if abs(gain) < 0.2:
        return audio, 0.0   # already close enough

    return audio.apply_gain(gain), gain


def trim_trailing_silence(
    audio: AudioSegment,
    *,
    silence_thresh: int = _PAUSE_COMPRESS_THRESH,
    min_silence_len: int = _MIN_SILENCE_LEN,
) -> tuple[AudioSegment, int]:
    """Remove only trailing silence — never cut speech content."""
    if len(audio) < min_silence_len:
        return audio, 0

    ranges = detect_nonsilent(
        audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh
    )
    if not ranges:
        return audio, 0

    last_end = int(ranges[-1][1])
    if last_end >= len(audio):
        return audio, 0

    trimmed_ms = len(audio) - last_end
    if trimmed_ms < min_silence_len:
        return audio, 0
    return audio[:last_end], trimmed_ms


def prepare_dub_segment_audio(
    tts_path: str | Path,
    slot_ms: int,
    work_dir: Path,
    *,
    max_atempo: float = DUB_MAX_ATEMPO,
    tolerance_ms: int = DUB_SLOT_TOLERANCE_MS,
) -> tuple[str, dict]:
    """
    Dub slot-fit step 1: loudness normalize + trailing silence trim +
    internal pause compress + atempo (cap max_atempo).
    Never trims speech — overflow after stretch is handled by text compress / TTS regen upstream.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    src = Path(tts_path)
    audio = AudioSegment.from_file(str(src))
    orig_ms = len(audio)

    # Normalize loudness FIRST so all segments reach the mix at consistent volume.
    audio, norm_gain = normalize_segment_loudness(audio)
    if abs(norm_gain) > 0.1:
        logger.debug("timing_fit: loudness gain=%.1f dB for %s", norm_gain, src.name)

    audio, tail_trim_ms = trim_trailing_silence(audio)
    compressed, pause_compressed_ms = compress_internal_pauses(audio)
    if pause_compressed_ms > 0:
        audio = compressed

    cur_ms = len(audio)
    atempo = 1.0
    strategy = "tail_trim" if tail_trim_ms else "none"
    if pause_compressed_ms > 0:
        strategy = strategy + "+pause_compress" if strategy != "none" else "pause_compress"

    cap = max(1.0, min(float(max_atempo), DUB_MAX_ATEMPO))
    if cur_ms > max(slot_ms, 1):
        need = cur_ms / max(slot_ms, 1)
        atempo = _gentle_atempo_factor(need, max_atempo=cap)
        if atempo > 1.001:
            tmp_in = work_dir / f"{src.stem}_pre_stretch.wav"
            tmp_out = work_dir / f"{src.stem}_stretched.wav"
            audio.export(tmp_in, format="wav")
            _atempo(tmp_in, atempo, tmp_out, max_atempo=cap)
            audio = AudioSegment.from_file(str(tmp_out))
            cur_ms = len(audio)
            strategy = strategy + "+atempo" if strategy != "none" else "atempo"

    out = work_dir / f"{src.stem}_prepared.wav"
    audio.export(out, format="wav")
    slot_limit = slot_ms + tolerance_ms
    return str(out), {
        "slot_ms": slot_ms,
        "slot_limit_ms": slot_limit,
        "tts_ms": orig_ms,
        "fitted_ms": cur_ms,
        "atempo": round(atempo, 4),
        "tail_trim_ms": tail_trim_ms,
        "pause_compressed_ms": pause_compressed_ms,
        "strategy": strategy,
        "overflow_ms": max(0, cur_ms - slot_limit),
    }


def compress_internal_pauses(
    audio: AudioSegment,
    *,
    silence_thresh: int = _PAUSE_COMPRESS_THRESH,
    min_silence_len: int = _MIN_SILENCE_LEN,
    max_pause_ms: int = _MAX_INTERNAL_PAUSE,
) -> tuple[AudioSegment, int]:
    """Укорачивает длинные паузы внутри реплики, не трогая темп речи."""
    if len(audio) < min_silence_len * 2:
        return audio, 0

    ranges = detect_nonsilent(
        audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh
    )
    if not ranges:
        return audio, 0

    out = AudioSegment.empty()
    prev_end = 0
    saved = 0

    for start, end in ranges:
        if start > prev_end:
            gap = start - prev_end
            if gap > min_silence_len:
                if gap >= _PRESERVE_PAUSE_MS:
                    new_gap = min(gap, max(max_pause_ms + 80, _PRESERVE_PAUSE_MS))
                else:
                    new_gap = min(gap, max_pause_ms)
                saved += gap - new_gap
                out += AudioSegment.silent(duration=new_gap)
            else:
                out += audio[prev_end:start]
        out += audio[start:end]
        prev_end = end

    if prev_end < len(audio):
        tail = len(audio) - prev_end
        if tail > min_silence_len:
            if tail >= _PRESERVE_PAUSE_MS:
                new_tail = min(tail, max(max_pause_ms + 80, _PRESERVE_PAUSE_MS))
            else:
                new_tail = min(tail, max_pause_ms)
            saved += tail - new_tail
            out += AudioSegment.silent(duration=new_tail)

    if saved <= 0:
        return audio, 0
    return out, saved


def _gentle_atempo_factor(need: float, *, max_atempo: float = _ATEMPO_ABSOLUTE_MAX) -> float:
    """
    Minimal speech speed-up — LAST RESORT; never exceed 1.05x.
    need = tts_ms / slot_ms (>1 means doesn't fit).
    Gentle curve: at 1.10 overflow we still only apply 1.04x
    so the listener never perceives a "rushed" delivery.
    """
    cap = max(1.0, min(_ATEMPO_ABSOLUTE_MAX, float(max_atempo)))
    if need <= 1.0:
        return 1.0
    # Very soft ramp: stay under 1.03 for small overflows
    if need <= 1.04:
        return min(need, min(1.02, cap))
    if need <= 1.08:
        return min(need, min(1.04, cap))
    # For anything larger: apply cap (1.05) and let gap_absorb or shorten handle the rest
    return min(need, cap)


def _atempo(in_path: Path, tempo: float, out_path: Path, *, max_atempo: float = _ATEMPO_ABSOLUTE_MAX) -> None:
    cap = max(_ATEMPO_MIN, min(_ATEMPO_ABSOLUTE_MAX, float(max_atempo)))
    tempo = max(_ATEMPO_MIN, min(cap, tempo))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    subprocess.run(
        [ffmpeg, "-y", "-i", str(in_path), "-filter:a", f"atempo={tempo:.4f}", str(out_path)],
        check=True,
        capture_output=True,
    )


def fit_segment_audio(
    tts_path: str | Path,
    slot_start: int,
    slot_end: int,
    next_start: int | None = None,
    work_dir: Path | None = None,
    *,
    allow_atempo: bool = False,
    max_atempo: float = _ATEMPO_ABSOLUTE_MAX,
    lead_in_ms: int = 0,
    word_map: dict | None = None,
    _skip_soft_sync: bool = False,
    no_speech_trim: bool = False,
    text_hint: str = "",
) -> tuple[str, dict]:
    """
    Подгонка TTS к слоту.
    По умолчанию allow_atempo=False — скорость голоса НЕ меняется (TZ №2).
    atempo только если явно разрешён после адаптации перевода и merge.

    text_hint — ending text of the segment, used for natural post-sentence pause
    instead of full-slot silence padding.
    """
    slot_ms = max(0, slot_end - slot_start)
    pause_ms = max(0, (next_start - slot_end)) if next_start is not None else 0
    src = Path(tts_path)
    work = work_dir or Path(tempfile.mkdtemp(prefix="timing_fit_"))
    work.mkdir(parents=True, exist_ok=True)

    orig_ms = len(AudioSegment.from_file(str(src)))
    cur = src
    atempo = 1.0
    pause_borrowed_ms = 0
    pause_compressed_ms = 0
    strategy = "none"

    if slot_ms <= 0:
        out = work / f"{src.stem}_fitted.wav"
        AudioSegment.silent(duration=0).export(out, format="wav")
        return str(out), {
            "slot_ms": slot_ms,
            "tts_ms": orig_ms,
            "atempo": atempo,
            "pause_added_ms": 0,
            "pause_borrowed_ms": 0,
            "pause_compressed_ms": 0,
            "strategy": strategy,
            "overflow_ms": 0,
        }

    if not _skip_soft_sync:
        try:
            from engines.soft_sync import apply_soft_stretch_end, is_soft_sync_enabled

            if is_soft_sync_enabled():
                fitted, soft_meta = apply_soft_stretch_end(
                    cur,
                    slot_start,
                    slot_end,
                    work,
                    word_map=word_map,
                )
                soft_meta["tts_ms"] = orig_ms
                return fitted, soft_meta
        except Exception:
            pass

    audio = AudioSegment.from_file(str(cur))
    cur_ms = len(audio)
    effective_slot = slot_ms

    if lead_in_ms > 0:
        audio = AudioSegment.silent(duration=min(650, int(lead_in_ms))) + audio
        cur_ms = len(audio)
        strategy = "lead_in"

    if cur_ms > effective_slot and pause_ms > 0:
        borrow = min(pause_ms, _MAX_BORROW_MS, cur_ms - effective_slot)
        effective_slot += borrow
        pause_borrowed_ms = borrow
        if borrow > 0:
            strategy = "pause_borrow"

    if cur_ms > effective_slot:
        compressed, saved = compress_internal_pauses(audio)
        if saved > 0:
            pause_compressed_ms = saved
            tmp_pause = work / f"{src.stem}_pausecmp.wav"
            compressed.export(tmp_pause, format="wav")
            cur, audio, cur_ms = tmp_pause, compressed, len(compressed)
            strategy = "pause_compress" if strategy == "none" else strategy + "+pause_compress"

    if cur_ms > effective_slot and allow_atempo:
        need = cur_ms / max(effective_slot, 1)
        atempo = _gentle_atempo_factor(need, max_atempo=max_atempo)
        if atempo > 1.001:
            tmp = work / f"{src.stem}_spd.wav"
            _atempo(cur, atempo, tmp, max_atempo=max_atempo)
            cur, audio, cur_ms = (
                tmp,
                AudioSegment.from_file(str(tmp)),
                len(AudioSegment.from_file(str(tmp))),
            )
            strategy = "atempo_gentle" if strategy == "none" else strategy + "+atempo_gentle"
            logger.info(
                "timing_fit: gentle atempo=%.3f (need=%.3f, slot=%dms, tts=%dms)",
                atempo,
                need,
                effective_slot,
                orig_ms,
            )
    elif cur_ms > effective_slot and not allow_atempo:
        logger.debug(
            "timing_fit: overflow %dms — atempo skipped (adapt translation first)",
            cur_ms - effective_slot,
        )

    fitted_ms = len(audio)
    pause_added_ms = 0

    hard_cap = effective_slot
    if next_start is not None:
        hard_cap = min(hard_cap, max(180, next_start - slot_start))
    if fitted_ms > hard_cap and not no_speech_trim:
        audio = audio[:hard_cap]
        fitted_ms = len(audio)
        strategy = strategy + "+trim_overlap" if strategy != "none" else "trim_overlap"
    elif fitted_ms > hard_cap and no_speech_trim:
        strategy = strategy + "+no_trim_overflow" if strategy != "none" else "no_trim_overflow"
        logger.debug(
            "timing_fit: speech trim skipped (no_speech_trim) overflow=%dms hard_cap=%dms",
            fitted_ms - hard_cap,
            hard_cap,
        )

    if fitted_ms < effective_slot:
        # Add only a natural post-sentence pause (80–220 ms based on punctuation).
        # We do NOT fill the entire slot with silence — that creates dead air after speech.
        # FFmpeg adelay handles exact timeline placement; audio length can be shorter than slot.
        natural_pause = natural_sentence_pause_ms(text_hint)
        remaining = effective_slot - fitted_ms
        pause_added_ms = min(natural_pause, remaining)
        if pause_added_ms > 0:
            audio = audio + AudioSegment.silent(duration=pause_added_ms)
    elif fitted_ms > effective_slot:
        strategy = strategy + "+overflow" if strategy != "none" else "overflow"
        logger.debug(
            "timing_fit: segment overflow %dms > window %dms (atempo=%.2f)",
            fitted_ms,
            effective_slot,
            atempo,
        )

    overflow_ms = max(0, fitted_ms - effective_slot)

    out = work / f"{src.stem}_fitted.wav"
    audio.export(out, format="wav")
    return str(out), {
        "slot_ms": slot_end - slot_start,
        "effective_slot_ms": effective_slot,
        "tts_ms": orig_ms,
        "fitted_ms": len(audio),
        "atempo": round(atempo, 4),
        "pause_added_ms": pause_added_ms,
        "pause_borrowed_ms": pause_borrowed_ms,
        "pause_compressed_ms": pause_compressed_ms,
        "inter_pause_ms": pause_ms,
        "strategy": strategy,
        "overflow_ms": overflow_ms,
    }


def _segment_start_delays(
    count: int,
    base_ms: int,
    jitter_ms: int = 0,
) -> list[int]:
    if count <= 0 or base_ms <= 0:
        return [0] * max(count, 0)
    out: list[int] = []
    for i in range(count):
        if jitter_ms <= 0:
            out.append(base_ms)
            continue
        spread = (i * 37 + 13) % (2 * jitter_ms + 1)
        out.append(max(0, base_ms + spread - jitter_ms))
    return out


def _mix_fitted_segments_ffmpeg(
    fitted: list[tuple[str, int, int]],
    master_ms: int,
    work_dir: Path,
) -> Path | None:
    """Single-pass ffmpeg amix — avoids O(n²) pydub overlay on long tracks."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not fitted:
        return None

    out_path = work_dir / "master_mix.wav"
    duration_sec = max(1.0, master_ms / 1000.0)
    cmd: list[str] = [ffmpeg, "-y"]
    for path, _, _ in fitted:
        cmd.extend(["-i", str(path)])

    filter_parts: list[str] = []
    mix_labels: list[str] = []
    for i, (_, place_start, _) in enumerate(fitted):
        delay = max(0, int(place_start))
        label = f"s{i}"
        filter_parts.append(f"[{i}:a]adelay={delay}|{delay}[{label}]")
        mix_labels.append(f"[{label}]")

    n_seg = len(fitted)
    # normalize=0: do not divide by input count (default amix makes dub ~1/N quiet).
    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={n_seg}:duration=longest:"
        f"dropout_transition=0:normalize=0[outmix];"
        f"[outmix]apad=whole_dur={duration_sec:.3f}[out]"
    )
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            str(out_path),
        ]
    )
    try:
        timeout = max(120, int(duration_sec * 3) + len(fitted) * 10)
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        return out_path if out_path.is_file() else None
    except Exception as exc:
        logger.warning("timing_fit: ffmpeg mix fallback to pydub: %s", exc)
        return None


def build_gap_adjusted_track(
    segment_paths: list[str | Path],
    timing_map: list[Any],
    video_duration_ms: int | None = None,
    log_path: str | Path | None = None,
    task_id: str | None = None,
    allow_atempo_flags: list[bool] | None = None,
    start_delays_ms: list[int] | None = None,
    lead_in_ms_list: list[int] | None = None,
    text_hints: list[str] | None = None,
    max_atempo: float = _ATEMPO_ABSOLUTE_MAX,
    on_segment_progress=None,
) -> tuple[AudioSegment, list[str], dict]:
    """Continuous silence base; overlay fitted segments at exact starts."""
    if not segment_paths or not timing_map:
        raise RuntimeError("segment_paths and timing_map required")

    n = min(len(segment_paths), len(timing_map))
    parsed = [_parse_timing(timing_map[i]) for i in range(n)]
    max_end = max(e for _, e in parsed) if parsed else 0
    master_ms = max(max_end, int(video_duration_ms or 0))
    if master_ms <= 0:
        raise RuntimeError("invalid master duration")
    master_ms += 500

    log_lines: list[str] = []
    fitted_placements: list[dict] = []
    work_dir = Path(tempfile.mkdtemp(prefix="timing_fit_track_"))
    fitted_for_mix: list[tuple[str, int, int]] = []

    try:
        for i in range(n):
            start, end = parsed[i]
            next_start = parsed[i + 1][0] if i + 1 < n else None
            delay = (
                int(start_delays_ms[i])
                if start_delays_ms and i < len(start_delays_ms)
                else 0
            )
            place_start = start + max(0, delay)
            allow_spd = bool(
                allow_atempo_flags[i]
                if allow_atempo_flags and i < len(allow_atempo_flags)
                else False
            )
            lead_in = (
                int(lead_in_ms_list[i])
                if lead_in_ms_list and i < len(lead_in_ms_list)
                else 0
            )
            text_h = (text_hints[i] if text_hints and i < len(text_hints) else "")
            fitted_path, meta = fit_segment_audio(
                segment_paths[i],
                start,
                end,
                next_start,
                work_dir=work_dir,
                allow_atempo=allow_spd,
                max_atempo=max_atempo,
                lead_in_ms=lead_in,
                text_hint=text_h,
            )
            seg = AudioSegment.from_file(fitted_path)
            fitted_ms = len(seg)
            fitted_for_mix.append((fitted_path, place_start, fitted_ms))
            fitted_placements.append(
                {
                    "idx": i,
                    "place_start": place_start,
                    "original_start_ms": start,
                    "slot_end_ms": end,
                    "delay_ms": delay,
                    "fitted_ms": fitted_ms,
                    "pause_compressed_ms": meta.get("pause_compressed_ms", 0),
                    "strategy": meta.get("strategy", "none"),
                    "overflow_ms": meta.get("overflow_ms", 0),
                    "atempo": meta.get("atempo", 1.0),
                }
            )
            log_lines.append(
                f"idx={i} slot_ms={meta['slot_ms']} tts_ms={meta['tts_ms']} "
                f"atempo={meta['atempo']} pause_added_ms={meta['pause_added_ms']} "
                f"pause_borrowed_ms={meta['pause_borrowed_ms']} "
                f"pause_compressed_ms={meta.get('pause_compressed_ms', 0)} "
                f"strategy={meta.get('strategy', 'none')} overflow_ms={meta.get('overflow_ms', 0)} "
                f"inter_pause_ms={meta['inter_pause_ms']} place_start={place_start} delay_ms={delay}"
            )
            if on_segment_progress:
                try:
                    on_segment_progress(i + 1, n)
                except Exception:
                    pass

        max_end = master_ms
        for _, place_start, fitted_ms in fitted_for_mix:
            max_end = max(max_end, place_start + fitted_ms)
        master_ms = max_end + 500

        from engines.conflict_resolver import apply_resolver_to_fitted

        resolver_result = apply_resolver_to_fitted(
            fitted_placements,
            fitted_for_mix,
            timing_map[:n],
            task_id=task_id,
        )
        overlap_report_extra = {
            "conflict_resolver": resolver_result.intervention_map,
            "conflict_resolver_profile": resolver_result.profile,
            "conflict_strategy_counts": resolver_result.strategy_counts,
        }

        ffmpeg_out = _mix_fitted_segments_ffmpeg(fitted_for_mix, master_ms, work_dir)
        if ffmpeg_out:
            master = AudioSegment.from_file(str(ffmpeg_out))
        else:
            master = AudioSegment.silent(duration=master_ms)
            for path, place_start, _ in fitted_for_mix:
                seg = AudioSegment.from_file(path)
                if place_start + len(seg) > len(master):
                    master = master + AudioSegment.silent(
                        duration=place_start + len(seg) + 500 - len(master)
                    )
                master = master.overlay(seg, position=place_start)

        from engines.overlap_quality import build_quality_report, detect_fitted_overlaps

        fitted_overlaps = detect_fitted_overlaps(fitted_placements)
        overlap_report = build_quality_report([], fitted_overlaps, fitted_placements)
        overlap_report.update(overlap_report_extra)

        if log_path:
            p = Path(log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            header = f"=== task={task_id} ===\n" if task_id else ""
            with open(p, "a", encoding="utf-8") as f:
                f.write(header + "\n".join(log_lines) + "\n")

        logger.info(
            "timing_fit: %d segments, master=%d ms, overlaps=%d",
            n,
            master_ms,
            len(fitted_overlaps),
        )
        return master, log_lines, overlap_report
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
