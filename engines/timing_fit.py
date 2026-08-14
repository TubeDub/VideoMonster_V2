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

# ─── Quality-first constants (TZ text-fit / Stage 15–19) ──────────────────────
# meaning completeness > timing fit > atempo (Happy Path ≤1.15, ≥0.85 slow).
# Stage 28 §D1: UK Simple pipeline runtime caps to 1.05 via the
# `max_atempo` argument in `apply_simple_pipeline_policy` (not globally, so
# non-UK / advanced paths keep their existing budget).
_ATEMPO_MIN = 0.85               # Stage 19 MAX_ATEMPO_SLOW
_ATEMPO_ABSOLUTE_MAX = 1.20      # hard ceiling (advanced / legacy)
_ATEMPO_EMERGENCY_MAX = 1.20
DUB_MAX_ATEMPO = 1.15            # preferred per-segment cap (Simple)
HAPPY_PATH_MAX_ATEMPO = 1.15
DUB_SLOT_TOLERANCE_MS = 75
# Stage 17/19: max silence between consecutive speech placements.
MAX_INTER_SEG_DEAD_AIR_MS = 350
MAX_MICRO_PAUSE_MS = 150
UNDERFILL_STRETCH_RATIO = 0.90

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
_MAX_NATURAL_PAUSE_MS = 200

# Significant underfill: TTS fills < 80% of slot → noticeable dead air (Stage 5).
UNDERFILL_SIGNIFICANT_THRESH = 0.80
# Tail after speech when shrinking an underfilled slot (natural pause only).
_SLOT_SHRINK_PAD_MIN_MS = 80
_SLOT_SHRINK_PAD_MAX_MS = 200


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
    if slot_ms <= 0 or tts_ms <= 0:
        return False
    return tts_ms < slot_ms * UNDERFILL_SIGNIFICANT_THRESH


def underfill_metrics(tts_ms: int, slot_ms: int) -> dict[str, Any]:
    """Per-segment fill diagnostics (Stage 5)."""
    slot = max(0, int(slot_ms or 0))
    tts = max(0, int(tts_ms or 0))
    fill_ratio = (float(tts) / float(slot)) if slot > 0 else 0.0
    underfill_ms = max(0, slot - tts)
    significant = bool(slot > 0 and tts > 0 and fill_ratio < UNDERFILL_SIGNIFICANT_THRESH)
    return {
        "slot_ms": slot,
        "tts_ms": tts,
        "fill_ratio": round(fill_ratio, 4),
        "underfill_ms": underfill_ms,
        "underfill_significant": significant,
    }


def shrink_underfilled_slot_end(
    slot_start: int,
    slot_end: int,
    speech_ms: int,
    *,
    next_start: int | None = None,
    text_hint: str = "",
) -> tuple[int, dict[str, Any]]:
    """Shrink slot end toward speech end when underfilled — no long dead-air pad.

    end ≈ start + speech_ms + 80–200ms; never collide with the next segment.
    Does not slow the voice (atempo stays ≥ 0.95 elsewhere).
    """
    start = int(slot_start)
    end = int(slot_end)
    slot_ms = max(0, end - start)
    speech = max(0, int(speech_ms or 0))
    meta = {
        "slot_shrunk": False,
        "slot_end_before": end,
        "slot_end_after": end,
        "slot_ms_before": slot_ms,
        "slot_ms_after": slot_ms,
    }
    if slot_ms <= 0 or speech <= 0:
        return end, meta
    fill_ratio = speech / float(slot_ms)
    if fill_ratio >= UNDERFILL_SIGNIFICANT_THRESH:
        return end, meta

    pad = natural_sentence_pause_ms(text_hint)
    pad = max(_SLOT_SHRINK_PAD_MIN_MS, min(_SLOT_SHRINK_PAD_MAX_MS, pad))
    new_end = start + speech + pad
    if next_start is not None:
        new_end = min(new_end, max(start + 180, int(next_start) - 20))
    new_end = max(start + min(speech, slot_ms), min(new_end, end))
    if new_end >= end:
        return end, meta
    meta.update(
        {
            "slot_shrunk": True,
            "slot_end_after": int(new_end),
            "slot_ms_after": int(new_end - start),
            "shrink_pad_ms": pad,
            "fill_ratio_before": round(fill_ratio, 4),
        }
    )
    return int(new_end), meta


def _parse_timing(item: Any) -> tuple[int, int]:
    if isinstance(item, dict):
        start = item.get("start", item.get("start_ms", 0))
        end = item.get("end", item.get("end_ms", 0))
        return int(start or 0), int(end or 0)
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


def trim_audio_to_cap_word_safe(
    audio: AudioSegment,
    hard_cap_ms: int,
    *,
    lookback_ms: int = 520,
    min_keep_ms: int = 180,
    silence_thresh: int = _PAUSE_COMPRESS_THRESH,
    min_silence_len: int = 55,
    fade_ms: int = 45,
) -> tuple[AudioSegment, str]:
    """Cap overflow audio at a silence / word boundary — avoid mid-phoneme chops.

    Hard ``audio[:hard_cap]`` cuts spoken syllables in half (user hears «маши»,
    «історі», «зосередитис»). Prefer the end of the last nonsilent chunk that
    finishes before the cap; only fall back to a raw hard cut when no pause
    exists in the lookback window.
    """
    cap = max(0, int(hard_cap_ms))
    if cap <= 0:
        return AudioSegment.silent(duration=0), "trim_overlap_empty"
    if len(audio) <= cap:
        return audio, "none"

    window_start = max(int(min_keep_ms), cap - max(80, int(lookback_ms)))
    head = audio[:cap]
    ranges = detect_nonsilent(
        head, min_silence_len=min_silence_len, silence_thresh=silence_thresh
    )
    cut_at = cap
    tag = "trim_overlap_hard"
    if ranges:
        last_start, last_end = int(ranges[-1][0]), int(ranges[-1][1])
        # Silence already at the tail of the cap window — cut after speech.
        if last_end < cap - 12 and last_end >= min_keep_ms:
            cut_at = last_end
            tag = "trim_overlap_silence"
        else:
            # Speech crosses the boundary: drop the overflowing chunk, keep
            # the previous complete nonsilent span ending inside lookback.
            cut_candidate: int | None = None
            for i in range(len(ranges) - 1, -1, -1):
                _s, end_i = int(ranges[i][0]), int(ranges[i][1])
                if end_i <= cap and end_i >= window_start and end_i >= min_keep_ms:
                    # If this chunk itself runs into the cap, prefer previous.
                    if end_i >= cap - 12 and i > 0:
                        prev_end = int(ranges[i - 1][1])
                        if prev_end >= min_keep_ms:
                            cut_candidate = prev_end
                            break
                    cut_candidate = end_i
                    break
            if cut_candidate is None and last_start >= min_keep_ms and last_start < cap:
                # Cut at the start of the overflowing word/chunk.
                cut_candidate = last_start
            if cut_candidate is not None and cut_candidate >= min_keep_ms:
                cut_at = cut_candidate
                tag = "trim_overlap_word_boundary"

    out = audio[: max(min_keep_ms, min(cut_at, cap))]
    if fade_ms > 0 and len(out) > fade_ms + 30:
        out = out.fade_out(min(int(fade_ms), max(20, len(out) // 5)))
    return out, tag


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


def _atempo_hard_cap(max_atempo: float) -> float:
    """Never exceed TZ Stage 3 ceiling (1.20)."""
    requested = float(max_atempo)
    ceiling = float(_ATEMPO_ABSOLUTE_MAX)
    return max(1.0, min(ceiling, requested))


def _gentle_atempo_factor(need: float, *, max_atempo: float = _ATEMPO_ABSOLUTE_MAX) -> float:
    """
    Minimal speech speed-up — LAST RESORT.
    Soft steps toward need, hard-capped at max_atempo (Happy Path ≤1.15).
    """
    cap = _atempo_hard_cap(max_atempo)
    if need <= 1.0:
        return 1.0
    if need <= 1.03:
        return min(need, min(1.02, cap))
    if need <= 1.06:
        return min(need, min(1.04, cap))
    if need <= 1.10:
        return min(need, min(1.06, cap))
    return min(need, cap)


def _gentle_atempo_slow_factor(
    fill_need: float,
    *,
    min_atempo: float = _ATEMPO_MIN,
) -> float:
    """Slow speech to fill dead air. fill_need = target_ms / speech_ms (≥1)."""
    if fill_need <= 1.001:
        return 1.0
    # atempo < 1 lengthens audio: tempo = speech/target = 1/fill_need
    tempo = 1.0 / float(fill_need)
    floor = max(0.85, float(min_atempo))
    return max(floor, min(1.0, tempo))


def _atempo(
    in_path: Path,
    tempo: float,
    out_path: Path,
    *,
    max_atempo: float = _ATEMPO_ABSOLUTE_MAX,
    min_atempo: float = _ATEMPO_MIN,
) -> None:
    hi = _atempo_hard_cap(max_atempo)
    lo = max(0.85, float(min_atempo))
    tempo = max(lo, min(hi, float(tempo)))
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
        # Never exceed caller max_atempo (Happy Path passes ≤1.08).
        effective_max = min(float(max_atempo), float(_ATEMPO_ABSOLUTE_MAX))
        atempo = _gentle_atempo_factor(need, max_atempo=effective_max)
        if atempo > 1.001:
            tmp = work / f"{src.stem}_spd.wav"
            _atempo(cur, atempo, tmp, max_atempo=effective_max)
            cur, audio, cur_ms = (
                tmp,
                AudioSegment.from_file(str(tmp)),
                len(AudioSegment.from_file(str(tmp))),
            )
            tag = "atempo_gentle"
            strategy = tag if strategy == "none" else strategy + f"+{tag}"
            logger.info(
                "timing_fit: %s atempo=%.3f (need=%.3f, slot=%dms, tts=%dms, cap=%.3f)",
                tag,
                atempo,
                need,
                effective_slot,
                orig_ms,
                effective_max,
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
        # Before chopping speech: emergency atempo toward hard_cap so we cut
        # fewer trailing words (and avoid mid-syllable hard clips).
        # TZ №2: never speed up speech unless atempo was explicitly allowed —
        # default path must keep atempo == 1.0 (no robotic acceleration).
        need_cap = fitted_ms / max(hard_cap, 1)
        if allow_atempo and need_cap > 1.02:
            emergency = _gentle_atempo_factor(
                need_cap, max_atempo=_ATEMPO_EMERGENCY_MAX
            )
            if emergency > 1.001:
                tmp_em = work / f"{src.stem}_spd_cap.wav"
                try:
                    _atempo(cur, emergency, tmp_em, max_atempo=_ATEMPO_EMERGENCY_MAX)
                    cur = tmp_em
                    audio = AudioSegment.from_file(str(tmp_em))
                    fitted_ms = len(audio)
                    atempo = max(float(atempo), float(emergency))
                    tag = "atempo_pre_trim"
                    strategy = tag if strategy == "none" else strategy + f"+{tag}"
                except Exception as exc:
                    logger.debug("timing_fit: emergency atempo before trim failed: %s", exc)
        if fitted_ms > hard_cap:
            audio, trim_tag = trim_audio_to_cap_word_safe(audio, hard_cap)
            fitted_ms = len(audio)
            # Always keep legacy "trim_overlap" token for callers/tests.
            extra = trim_tag if trim_tag != "trim_overlap_hard" else "trim_overlap"
            if strategy == "none":
                strategy = extra if extra == "trim_overlap" else f"trim_overlap+{extra}"
            else:
                strategy = strategy + "+trim_overlap"
                if extra != "trim_overlap":
                    strategy = strategy + f"+{extra}"
            logger.info(
                "timing_fit: %s hard_cap=%dms fitted=%dms (was overflow)",
                trim_tag,
                hard_cap,
                fitted_ms,
            )
    elif fitted_ms > hard_cap and no_speech_trim:
        # Happy Path: never chop words — try atempo ≤ caller max (≤1.08), else overflow.
        need_cap = fitted_ms / max(hard_cap, 1)
        if allow_atempo and need_cap > 1.02:
            cap = min(float(max_atempo), float(_ATEMPO_ABSOLUTE_MAX))
            emergency = _gentle_atempo_factor(need_cap, max_atempo=cap)
            if emergency > 1.001:
                tmp_em = work / f"{src.stem}_spd_cap.wav"
                try:
                    _atempo(cur, emergency, tmp_em, max_atempo=cap)
                    cur = tmp_em
                    audio = AudioSegment.from_file(str(tmp_em))
                    fitted_ms = len(audio)
                    atempo = max(float(atempo), float(emergency))
                    tag = "atempo_no_trim"
                    strategy = tag if strategy == "none" else strategy + f"+{tag}"
                except Exception as exc:
                    logger.debug("timing_fit: atempo without trim failed: %s", exc)
        overflow_left = max(0, fitted_ms - hard_cap)
        strategy = (
            strategy + "+no_trim_overflow" if strategy != "none" else "no_trim_overflow"
        )
        logger.warning(
            "timing_fit: NO speech trim — slot=%dms tts=%dms atempo=%.3f "
            "fitted=%dms overflow=%dms strategy=%s",
            hard_cap,
            orig_ms,
            atempo,
            fitted_ms,
            overflow_left,
            strategy,
        )

    # Speech length before natural pause pad — used to sync Review text after trim.
    speech_ms = int(fitted_ms)
    speech_trimmed = "trim_overlap" in str(strategy or "")

    # Stage 17: underfill → atempo-slow to fill slot (prefer stretch over dead air).
    if (
        fitted_ms > 0
        and effective_slot > 0
        and fitted_ms < int(effective_slot * UNDERFILL_STRETCH_RATIO)
        and allow_atempo
    ):
        fill_need = float(effective_slot) / float(max(fitted_ms, 1))
        slow = _gentle_atempo_slow_factor(fill_need, min_atempo=_ATEMPO_MIN)
        if slow < 0.999:
            tmp_slow = work / f"{src.stem}_slow.wav"
            try:
                _atempo(
                    cur,
                    slow,
                    tmp_slow,
                    max_atempo=max(1.0, float(max_atempo)),
                    min_atempo=_ATEMPO_MIN,
                )
                cur = tmp_slow
                audio = AudioSegment.from_file(str(tmp_slow))
                fitted_ms = len(audio)
                speech_ms = int(fitted_ms)
                atempo = float(slow)
                tag = "atempo_slow"
                strategy = tag if strategy == "none" else strategy + f"+{tag}"
                logger.info(
                    "timing_fit: atempo_slow=%.3f slot=%dms tts→%dms (kill dead air)",
                    slow,
                    effective_slot,
                    fitted_ms,
                )
            except Exception as exc:
                logger.debug("timing_fit: atempo_slow failed: %s", exc)

    if fitted_ms < effective_slot:
        # Micro pause only (≤150 ms). Residual underfill → gap closer / slow.
        natural_pause = natural_sentence_pause_ms(text_hint)
        remaining = effective_slot - fitted_ms
        pause_added_ms = min(
            natural_pause, remaining, MAX_MICRO_PAUSE_MS, _SLOT_SHRINK_PAD_MAX_MS
        )
        if pause_added_ms > 0:
            audio = audio + AudioSegment.silent(duration=pause_added_ms)
            fitted_ms = len(audio)
    elif fitted_ms > effective_slot:
        strategy = strategy + "+overflow" if strategy != "none" else "overflow"
        logger.debug(
            "timing_fit: segment overflow %dms > window %dms (atempo=%.2f)",
            fitted_ms,
            effective_slot,
            atempo,
        )

    overflow_ms = max(0, fitted_ms - effective_slot)
    # Clamp atempo into Happy Path band (0.95–1.15).
    if atempo < _ATEMPO_MIN:
        atempo = _ATEMPO_MIN
    if atempo > float(max_atempo):
        atempo = float(max_atempo)

    speech_for_fill = int(speech_ms)
    fill_orig = underfill_metrics(speech_for_fill, slot_end - slot_start)
    # Stage 17: prefer keeping full slot (stretch already applied). Shrink only
    # when stretch could not fill AND gap to next would stay ≤350ms anyway.
    gap_to_next = (
        max(0, int(next_start) - int(slot_end)) if next_start is not None else 10**9
    )
    allow_shrink = (
        fill_orig.get("underfill_significant")
        and "atempo_slow" not in str(strategy)
        and gap_to_next <= MAX_INTER_SEG_DEAD_AIR_MS
    )
    if allow_shrink:
        shrunk_end, shrink_meta = shrink_underfilled_slot_end(
            slot_start,
            slot_end,
            speech_for_fill,
            next_start=next_start,
            text_hint=text_hint,
        )
    else:
        shrunk_end, shrink_meta = slot_end, {
            "slot_shrunk": False,
            "slot_end_before": slot_end,
            "slot_end_after": slot_end,
            "slot_ms_before": slot_end - slot_start,
            "slot_ms_after": slot_end - slot_start,
        }
    if shrink_meta.get("slot_shrunk"):
        tag = "slot_shrink"
        strategy = tag if strategy == "none" else strategy + f"+{tag}"
    fill_eff = underfill_metrics(
        speech_for_fill, max(0, int(shrunk_end) - slot_start)
    )

    out = work / f"{src.stem}_fitted.wav"
    audio.export(out, format="wav")
    meta = {
        "slot_ms": slot_end - slot_start,
        "effective_slot_ms": effective_slot,
        "tts_ms": orig_ms,
        "speech_ms": speech_ms,
        "speech_trimmed": speech_trimmed,
        "fitted_ms": len(audio),
        "atempo": round(atempo, 4),
        "pause_added_ms": pause_added_ms,
        "pause_borrowed_ms": pause_borrowed_ms,
        "pause_compressed_ms": pause_compressed_ms,
        "inter_pause_ms": pause_ms,
        "strategy": strategy,
        "overflow_ms": overflow_ms,
        "no_speech_trim": bool(no_speech_trim),
        # Original-slot fill (acceptance); shrink is a structural fix, not success-by-silence.
        "fill_ratio": fill_orig.get("fill_ratio"),
        "underfill_ms": fill_orig.get("underfill_ms"),
        "underfill_significant": fill_orig.get("underfill_significant"),
        "fill_ratio_effective": fill_eff.get("fill_ratio"),
        "slot_shrunk": bool(shrink_meta.get("slot_shrunk")),
        "slot_end_ms": int(shrunk_end),
        "slot_ms_effective": int(max(0, shrunk_end - slot_start)),
        "underfill_resolved_by_shrink": bool(
            shrink_meta.get("slot_shrunk") and fill_orig.get("underfill_significant")
        ),
    }
    if fill_orig.get("underfill_significant") and not shrink_meta.get("slot_shrunk"):
        meta["underfill_unresolved"] = True
        logger.warning(
            "timing_fit: underfill unresolved slot=%dms speech=%dms fill=%.2f",
            slot_end - slot_start,
            speech_for_fill,
            float(fill_orig.get("fill_ratio") or 0),
        )
    logger.info(
        "timing_fit: slot_ms=%s tts_ms=%s atempo=%.3f overflow_ms=%s strategy=%s "
        "no_speech_trim=%s fill=%.2f shrink=%s",
        meta["slot_ms"],
        meta["tts_ms"],
        meta["atempo"],
        meta["overflow_ms"],
        meta["strategy"],
        meta["no_speech_trim"],
        float(meta.get("fill_ratio") or 0),
        meta.get("slot_shrunk"),
    )
    return str(out), meta


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


def en_speech_likely_in_interval(
    gap_start_ms: int,
    gap_end_ms: int,
    *,
    slot_start: int,
    slot_end: int,
    next_start: int | None,
    en_speech_intervals: list[tuple[int, int]] | None = None,
) -> bool:
    """True when the gap sits where EN had continuous speech (not a natural pause)."""
    a = int(gap_start_ms)
    b = int(gap_end_ms)
    if b <= a:
        return False
    # Underfill inside own slot → EN speech zone by definition.
    if a < int(slot_end) - 20:
        return True
    # Contiguous / short EN inter-seg pause (≤350 ms in timing map).
    if next_start is not None and int(next_start) - int(slot_end) <= MAX_INTER_SEG_DEAD_AIR_MS:
        return True
    if en_speech_intervals:
        for s, e in en_speech_intervals:
            if int(s) < b and int(e) > a:
                return True
    return False


def close_inter_segment_dead_air(
    fitted_for_mix: list[tuple[str, int, int]],
    fitted_placements: list[dict],
    work_dir: Path,
    *,
    en_speech_intervals: list[tuple[int, int]] | None = None,
    max_gap_ms: int = MAX_INTER_SEG_DEAD_AIR_MS,
    min_atempo: float = _ATEMPO_MIN,
) -> list[dict]:
    """Before mux: stretch seg i when placement gap >350ms on EN-speech zones.

    Never invents TTS text — only atempo-slow / micro-pause pad (≤150ms).
    Mutates fitted_for_mix and fitted_placements in place. Returns audit rows.
    """
    audits: list[dict] = []
    n = len(fitted_for_mix)
    if n < 2:
        return audits

    for i in range(n - 1):
        path_i, place_i, fitted_i = fitted_for_mix[i]
        place_next = int(fitted_for_mix[i + 1][1])
        audio_end = int(place_i) + int(fitted_i)
        gap = place_next - audio_end
        if gap <= int(max_gap_ms):
            continue

        row = fitted_placements[i] if i < len(fitted_placements) else {}
        slot_start = int(row.get("original_start_ms") or place_i)
        slot_end = int(row.get("slot_end_ms") or (slot_start + int(row.get("slot_ms") or 0)))
        next_start = int(
            (fitted_placements[i + 1].get("original_start_ms") if i + 1 < len(fitted_placements) else place_next)
            or place_next
        )
        if not en_speech_likely_in_interval(
            audio_end,
            place_next,
            slot_start=slot_start,
            slot_end=slot_end,
            next_start=next_start,
            en_speech_intervals=en_speech_intervals,
        ):
            audits.append(
                {
                    "idx": i,
                    "gap_ms": gap,
                    "action": "skip_en_pause",
                    "reason": "en_natural_pause",
                }
            )
            continue

        # Aim for ≤150ms micro-pause (or ≤350ms hard cap if stretch floor hits).
        target_end = place_next - MAX_MICRO_PAUSE_MS
        target_fitted = max(int(fitted_i), target_end - int(place_i))
        if target_fitted <= int(fitted_i):
            continue

        fill_need = float(target_fitted) / float(max(int(fitted_i), 1))
        slow = _gentle_atempo_slow_factor(fill_need, min_atempo=min_atempo)
        action = "none"
        new_path = path_i
        new_ms = int(fitted_i)
        new_next_place = int(place_next)
        if slow < 0.999 and Path(path_i).is_file():
            try:
                out = Path(work_dir) / f"gap_close_{i}_slow.wav"
                _atempo(
                    Path(path_i),
                    slow,
                    out,
                    max_atempo=1.0,
                    min_atempo=min_atempo,
                )
                new_path = str(out)
                new_ms = len(AudioSegment.from_file(new_path))
                action = "atempo_slow_gap"
            except Exception as exc:
                logger.debug("timing_fit: gap atempo_slow idx=%d: %s", i, exc)
        # Residual gap after stretch: micro-pad ≤150ms only.
        still_gap = new_next_place - (int(place_i) + new_ms)
        if still_gap > int(max_gap_ms) and new_ms > 0:
            pad = min(MAX_MICRO_PAUSE_MS, still_gap - int(max_gap_ms))
            if pad > 0:
                try:
                    audio = AudioSegment.from_file(new_path)
                    audio = audio + AudioSegment.silent(duration=pad)
                    out_pad = Path(work_dir) / f"gap_close_{i}_pad.wav"
                    audio.export(out_pad, format="wav")
                    new_path = str(out_pad)
                    new_ms = len(audio)
                    action = (
                        "atempo_slow_gap+micro_pad"
                        if action.startswith("atempo")
                        else "micro_pad"
                    )
                except Exception as exc:
                    logger.debug("timing_fit: gap micro_pad idx=%d: %s", i, exc)
        # Still >350ms → shift next boundary earlier (no invented speech).
        still_gap = new_next_place - (int(place_i) + new_ms)
        if still_gap > int(max_gap_ms):
            shifted = int(place_i) + new_ms + MAX_MICRO_PAUSE_MS
            if shifted < new_next_place:
                new_next_place = shifted
                path_n, _, ms_n = fitted_for_mix[i + 1]
                fitted_for_mix[i + 1] = (path_n, new_next_place, ms_n)
                if i + 1 < len(fitted_placements):
                    fitted_placements[i + 1]["place_start"] = new_next_place
                    prev_n = str(
                        fitted_placements[i + 1].get("strategy") or "none"
                    )
                    tag_n = "boundary_shift"
                    fitted_placements[i + 1]["strategy"] = (
                        tag_n if prev_n == "none" else prev_n + f"+{tag_n}"
                    )
                action = (
                    f"{action}+boundary_shift"
                    if action != "none"
                    else "boundary_shift"
                )

        fitted_for_mix[i] = (new_path, int(place_i), new_ms)
        new_gap = new_next_place - (int(place_i) + new_ms)
        if i < len(fitted_placements):
            fitted_placements[i]["fitted_ms"] = new_ms
            fitted_placements[i]["gap_close_ms"] = max(0, gap - new_gap)
            fitted_placements[i]["dead_air_ms"] = max(0, new_gap)
            prev_st = str(fitted_placements[i].get("strategy") or "none")
            tag = "gap_close"
            fitted_placements[i]["strategy"] = (
                tag if prev_st == "none" else prev_st + f"+{tag}"
            )
            if "atempo" in action:
                fitted_placements[i]["atempo"] = round(float(slow), 4)
        audits.append(
            {
                "idx": i,
                "gap_ms_before": gap,
                "gap_ms_after": new_gap,
                "action": action,
                "atempo": round(float(slow), 4) if "atempo" in action else 1.0,
                "fitted_ms_before": int(fitted_i),
                "fitted_ms_after": new_ms,
                "next_place_after": new_next_place,
            }
        )
        logger.info(
            "timing_fit: gap_close idx=%d gap %dms→%dms action=%s",
            i,
            gap,
            new_gap,
            action,
        )
    return audits


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
    video_ms = int(video_duration_ms or 0)
    # TZ: master length = source video (ffprobe). Pad silence to video_end;
    # never shrink the track to last-segment end when video is longer.
    if video_ms > 0:
        master_ms = video_ms
    else:
        master_ms = max(max_end, 1) + 500
    if master_ms <= 0:
        raise RuntimeError("invalid master duration")

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
                    "slot_end_ms": int(meta.get("slot_end_ms") or end),
                    "slot_ms": int(meta.get("slot_ms") or max(0, end - start)),
                    "slot_ms_effective": int(
                        meta.get("slot_ms_effective")
                        or meta.get("slot_ms")
                        or max(0, end - start)
                    ),
                    "delay_ms": delay,
                    "fitted_ms": fitted_ms,
                    "tts_ms": int(meta.get("tts_ms") or 0),
                    "speech_ms": int(meta.get("speech_ms") or fitted_ms),
                    "speech_trimmed": bool(meta.get("speech_trimmed")),
                    "pause_added_ms": int(meta.get("pause_added_ms") or 0),
                    "pause_compressed_ms": meta.get("pause_compressed_ms", 0),
                    "strategy": meta.get("strategy", "none"),
                    "overflow_ms": meta.get("overflow_ms", 0),
                    "atempo": meta.get("atempo", 1.0),
                    "no_speech_trim": bool(meta.get("no_speech_trim")),
                    "fill_ratio": meta.get("fill_ratio"),
                    "underfill_ms": meta.get("underfill_ms"),
                    "underfill_significant": bool(meta.get("underfill_significant")),
                    "slot_shrunk": bool(meta.get("slot_shrunk")),
                    "fill_ratio_effective": meta.get("fill_ratio_effective"),
                    "underfill_resolved_by_shrink": bool(
                        meta.get("underfill_resolved_by_shrink")
                    ),
                }
            )
            log_lines.append(
                f"idx={i} slot_ms={meta['slot_ms']} tts_ms={meta['tts_ms']} "
                f"atempo={meta['atempo']} pause_added_ms={meta['pause_added_ms']} "
                f"pause_borrowed_ms={meta['pause_borrowed_ms']} "
                f"pause_compressed_ms={meta.get('pause_compressed_ms', 0)} "
                f"strategy={meta.get('strategy', 'none')} overflow_ms={meta.get('overflow_ms', 0)} "
                f"fill_ratio={meta.get('fill_ratio')} underfill_ms={meta.get('underfill_ms')} "
                f"slot_shrunk={meta.get('slot_shrunk')} "
                f"inter_pause_ms={meta['inter_pause_ms']} place_start={place_start} delay_ms={delay}"
            )
            if on_segment_progress:
                try:
                    on_segment_progress(i + 1, n)
                except Exception:
                    pass
            # Per-seg dead-air estimate before gap-close (slot underfill tail).
            _dead = max(
                0,
                int(meta.get("slot_ms") or 0) - int(meta.get("speech_ms") or fitted_ms),
            )
            fitted_placements[-1]["dead_air_ms"] = _dead
            fitted_placements[-1]["dead_air_risk_ms"] = _dead
            if _dead > MAX_INTER_SEG_DEAD_AIR_MS:
                fitted_placements[-1]["dead_air_unresolved"] = True

        # Stage 17: close inter-segment holes >350ms where EN had speech.
        en_intervals = [(int(s), int(e)) for s, e in parsed]
        gap_audits = close_inter_segment_dead_air(
            fitted_for_mix,
            fitted_placements,
            work_dir,
            en_speech_intervals=en_intervals,
        )
        # Recompute dead_air after gap-close; mark unresolved if still >350.
        for place in fitted_placements:
            if not isinstance(place, dict):
                continue
            idx = int(place.get("idx") or 0)
            if idx + 1 >= len(fitted_for_mix):
                dead = int(place.get("dead_air_ms") or 0)
            else:
                p0, s0, m0 = fitted_for_mix[idx]
                p1 = int(fitted_for_mix[idx + 1][1])
                dead = max(0, p1 - (int(s0) + int(m0)))
                place["dead_air_ms"] = dead
            if dead > MAX_INTER_SEG_DEAD_AIR_MS:
                place["dead_air_unresolved"] = True
            else:
                place.pop("dead_air_unresolved", None)

        speech_end = 0
        for _, place_start, fitted_ms in fitted_for_mix:
            speech_end = max(speech_end, int(place_start) + int(fitted_ms))
        if video_ms > 0:
            # Lock to video: pad silence to video_end; do not grow past source.
            master_ms = video_ms
            tail_gap_ms = max(0, video_ms - speech_end)
        else:
            master_ms = max(max_end, speech_end) + 500
            tail_gap_ms = 0

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
            "gap_close_audits": gap_audits,
            "video_duration_ms": video_ms or None,
            "track_duration_ms": master_ms,
            "speech_end_ms": speech_end,
            "tail_gap_ms": tail_gap_ms,
        }

        ffmpeg_out = _mix_fitted_segments_ffmpeg(fitted_for_mix, master_ms, work_dir)
        if ffmpeg_out:
            master = AudioSegment.from_file(str(ffmpeg_out))
        else:
            master = AudioSegment.silent(duration=master_ms)
            for path, place_start, _ in fitted_for_mix:
                seg = AudioSegment.from_file(path)
                # Clip overlay that would extend past video master.
                if place_start >= master_ms:
                    continue
                if place_start + len(seg) > master_ms:
                    seg = seg[: max(1, master_ms - place_start)]
                master = master.overlay(seg, position=place_start)

        # Hard pad/trim to exact video length (± encoder grain).
        if video_ms > 0:
            cur = len(master)
            if cur < video_ms:
                master = master + AudioSegment.silent(duration=video_ms - cur)
            elif cur > video_ms:
                master = master[:video_ms]
            master_ms = len(master)
            overlap_report_extra["track_duration_ms"] = master_ms
            overlap_report_extra["tail_gap_ms"] = max(0, video_ms - speech_end)

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
            "timing_fit: %d segments, master=%d ms video=%s speech_end=%d tail_gap=%d overlaps=%d",
            n,
            master_ms,
            video_ms or "-",
            speech_end,
            tail_gap_ms,
            len(fitted_overlaps),
        )
        return master, log_lines, overlap_report
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
