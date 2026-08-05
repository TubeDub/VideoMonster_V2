"""Closed Loop Timing Engine — measure real TTS, then decide.

Pipeline per segment (independent, no cascade shift):
  TTS → Measure → Pause trim → Measure → Fits?
    YES → Accept
    NO  → LLM rewrite → TTS → Measure  (max 5 iterations)

All decisions use actual_duration_ms only — never predicted estimates.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.closed_loop_timing")

MAX_REWRITE_ITERATIONS = 5
OVERFLOW_THRESHOLD_MS = 100
# Stage 19b/19c: text-first when |TTS−slot| > 350 ms (was 450; pause-only Happy Path skipped fit).
UNDERFLOW_THRESHOLD_MS = 350
TEXT_FIT_DELTA_MS = 350
# Stage 19c: large overflow → sentence split (production).
OVERFLOW_SPLIT_ABS_MS = 3000
OVERFLOW_SPLIT_RATIO = 0.25
MIN_SPLIT_CHILD_MS = 800
# Cap children — conj-splitting EN into 12 orphans caused RUNTIME_INTEGRITY (task dce9b27b).
MAX_STAGE19C_SPLIT_CHILDREN = 4
# Stage 19e/19f/19g: post-restore / giant predicted text may split into more children.
MAX_STAGE19E_SPLIT_CHILDREN = 12
MAX_STAGE19F_SPLIT_CHILDREN = 12
MAX_STAGE19F_SPLIT_DEPTH = 4
MAX_STAGE19G_SPLIT_CHILDREN = 12
MAX_STAGE19G_SPLIT_DEPTH = 4
MAX_STAGE19H_SPLIT_CHILDREN = 12
MAX_STAGE19H_SPLIT_DEPTH = 4
MAX_STAGE19I_SPLIT_CHILDREN = 10
MAX_STAGE19I_SPLIT_DEPTH = 3
MAX_STAGE19J_SPLIT_CHILDREN = 14
MAX_STAGE19J_SPLIT_DEPTH = 5
MAX_STAGE21_SPLIT_CHILDREN = 14
MAX_STAGE21_SPLIT_DEPTH = 5
OVERLAP_TOLERANCE_MS = 40
TIMING_SCORE_GOAL = 95
STAGE19G_UNDERFILL_FILL_RATIO = 0.92
STAGE19G_OK_FILL_LO = 0.85
STAGE19G_OK_FILL_HI = 1.12
STAGE19H_OK_FILL_LO = 0.85
STAGE19H_OK_FILL_HI = 1.12
STAGE19I_OK_FILL_LO = 0.85
STAGE19I_OK_FILL_HI = 1.12
STAGE19J_OK_FILL_LO = 0.85
STAGE19J_OK_FILL_HI = 1.12
STAGE21_OK_FILL_LO = 0.85
STAGE21_OK_FILL_HI = 1.12
# Stage 22: tighter OK band for dead-air killer.
STAGE22_OK_FILL_LO = 0.90
STAGE22_OK_FILL_HI = 1.12
# Stage 23: production fill — length_scale first, then clean expand.
STAGE23_OK_FILL_LO = 0.92
STAGE23_OK_FILL_HI = 1.12
UNDERFLOW_TRIGGER_MS = 250
OVERFLOW_TRIGGER_MS = 350
OVERFLOW_FORCE_SPLIT_MS = 350
ATEMPO_MIN = 0.90
ATEMPO_MAX = 1.20
STAGE23_ATEMPO_SLOW_LO = 0.90
STAGE23_ATEMPO_SLOW_HI = 1.0

# Honest algorithm reasons that must not be overwritten by AudioOnly.
_TEXT_FIT_REASON_PREFIXES = (
    "TextSlotFitExpand",
    "TextSlotFitShorten",
    "TextSlotFitSplit",
    "TextThenAtemo",
    "TextSlotSplit",
)


class TextFitNoRegenError(RuntimeError):
    """text_changed but regen_fn is None — fail loud (Stage 19c §B)."""

    error_code = "PIPELINE_TEXT_FIT_NO_REGEN"


class NeedReTTS(RuntimeError):
    """Stage 19d: text restored/changed — caller must re-TTS before finalize."""

    def __init__(self, reason: str = "need_re_tts"):
        self.reason = str(reason or "need_re_tts")
        super().__init__(self.reason)


STAGE19D_HARD_DELTA_MS = 800


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = str(os.getenv(key, "1" if default else "0")).strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class TimingBudget:
    index: int
    slot_start: int = 0
    slot_end: int = 0
    slot_duration: int = 0
    tts_duration: int = 0
    measured_duration: int = 0
    delta: int = 0
    overflow: int = 0
    underflow: int = 0
    status: str = "pending"  # pending|ok|overflow|underflow|failed
    rewrite_iterations: int = 0
    pause_adjustments_ms: int = 0
    pause_stages: list[str] = field(default_factory=list)
    timing_score: float = 0.0
    rewrite_reason: str = ""
    final_status: str = "pending"
    original_duration: int = 0
    provider_fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_timing(entry) -> tuple[int, int]:
    from engines.timing_fit import _parse_timing as _pf

    return _pf(entry)


def measure_actual_ms(seg: dict, *, resolve_path: Callable[[str], str] | None = None) -> int:
    """Real audio duration — never an estimate."""
    path = str(seg.get("file") or seg.get("tts_file_path") or "").strip()
    if path and resolve_path:
        try:
            path = resolve_path(path) or path
        except Exception:
            pass
    if path:
        try:
            from engines.pipeline_integrity.tts_segment_fields import (
                measure_playback_duration_ms,
            )

            ms = int(measure_playback_duration_ms(path) or 0)
            if ms > 0:
                seg["playback_duration"] = ms
                seg["tts_ms"] = ms
                seg["actual_duration_ms"] = ms
                return ms
        except Exception as exc:
            logger.debug("measure_actual_ms failed for %s: %s", path, exc)
    for key in ("playback_duration", "tts_ms", "fitted_ms", "actual_duration_ms"):
        val = seg.get(key)
        if val is not None:
            try:
                return max(0, int(val))
            except (TypeError, ValueError):
                pass
    return 0


def compute_timing_score(
    *,
    slot_ms: int,
    actual_ms: int,
    overlap_ms: int = 0,
    speech_end_delta_ms: int | None = None,
) -> float:
    """Timing Score 0..100 from real measurements only."""
    if slot_ms <= 0:
        return 0.0
    overflow = max(0, actual_ms - slot_ms)
    underflow = max(0, slot_ms - actual_ms) if actual_ms > 0 else slot_ms
    # Soft underflow under threshold is fine; hard overflow hurts more.
    overflow_pen = min(50.0, (overflow / max(slot_ms, 1)) * 100.0)
    under_pen = 0.0
    if underflow > UNDERFLOW_THRESHOLD_MS:
        under_pen = min(30.0, ((underflow - UNDERFLOW_THRESHOLD_MS) / max(slot_ms, 1)) * 80.0)
    overlap_pen = min(40.0, (max(0, overlap_ms) / max(slot_ms, 1)) * 120.0)
    end_pen = 0.0
    if speech_end_delta_ms is not None and abs(speech_end_delta_ms) > OVERFLOW_THRESHOLD_MS:
        end_pen = min(20.0, abs(speech_end_delta_ms) / max(slot_ms, 1) * 40.0)
    score = 100.0 - overflow_pen - under_pen - overlap_pen - end_pen
    return round(max(0.0, min(100.0, score)), 1)


def build_timing_budget(
    seg: dict,
    idx: int,
    timing_map: list,
    *,
    actual_ms: int | None = None,
) -> TimingBudget:
    start_ms, end_ms = (
        _parse_timing(timing_map[idx]) if idx < len(timing_map) else (0, 3000)
    )
    slot_ms = max(1, end_ms - start_ms)
    measured = int(actual_ms if actual_ms is not None else measure_actual_ms(seg))
    delta = measured - slot_ms
    overflow = max(0, delta)
    underflow = max(0, -delta) if measured > 0 else 0
    if measured <= 0:
        status = "failed"
    elif overflow > OVERFLOW_THRESHOLD_MS:
        status = "overflow"
    elif underflow > UNDERFLOW_THRESHOLD_MS:
        status = "underflow"
    else:
        status = "ok"

    overlap_ms = 0
    if idx + 1 < len(timing_map) and measured > 0:
        next_start, _ = _parse_timing(timing_map[idx + 1])
        overlap_ms = max(0, (start_ms + measured) - next_start - OVERLAP_TOLERANCE_MS)

    score = compute_timing_score(
        slot_ms=slot_ms,
        actual_ms=measured,
        overlap_ms=overlap_ms,
        speech_end_delta_ms=slot_ms - measured if measured > 0 else None,
    )
    return TimingBudget(
        index=idx,
        slot_start=start_ms,
        slot_end=end_ms,
        slot_duration=slot_ms,
        tts_duration=measured,
        measured_duration=measured,
        delta=delta,
        overflow=overflow,
        underflow=underflow,
        status=status,
        original_duration=int(seg.get("first_tts_duration_ms") or measured),
        timing_score=score,
        final_status=status,
    )


def apply_dynamic_pause_engine(
    seg: dict,
    *,
    slot_ms: int,
    work_dir: Path,
    resolve_path: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Trim leading/trailing silence and compress internal pauses — no atempo.

    Returns meta with pause_adjustments_ms and updated file path when changed.
    """
    meta: dict[str, Any] = {
        "applied": False,
        "pause_adjustments_ms": 0,
        "stages": [],
        "before_ms": 0,
        "after_ms": 0,
    }
    src = str(seg.get("file") or seg.get("tts_file_path") or "").strip()
    if not src:
        return meta
    if resolve_path:
        try:
            src = resolve_path(src) or src
        except Exception:
            pass
    if not Path(src).is_file():
        return meta

    try:
        from pydub import AudioSegment
        from pydub.silence import detect_nonsilent

        from engines.timing_fit import compress_internal_pauses, trim_trailing_silence
    except Exception as exc:
        logger.debug("Dynamic Pause Engine unavailable: %s", exc)
        return meta

    try:
        audio = AudioSegment.from_file(src)
    except Exception as exc:
        logger.debug("pause engine load failed: %s", exc)
        return meta

    before_ms = len(audio)
    meta["before_ms"] = before_ms
    stages: list[str] = []
    saved = 0

    # Leading silence
    ranges = detect_nonsilent(audio, min_silence_len=120, silence_thresh=-42)
    if ranges and ranges[0][0] >= 120:
        lead = int(ranges[0][0])
        audio = audio[lead:]
        saved += lead
        stages.append(f"lead_trim:{lead}")

    audio, tail = trim_trailing_silence(audio)
    if tail > 0:
        saved += int(tail)
        stages.append(f"tail_trim:{tail}")

    compressed, pause_ms = compress_internal_pauses(audio)
    if pause_ms > 0:
        audio = compressed
        saved += int(pause_ms)
        stages.append(f"pause_compress:{pause_ms}")

    after_ms = len(audio)
    meta["after_ms"] = after_ms
    meta["stages"] = stages
    meta["pause_adjustments_ms"] = max(0, before_ms - after_ms)

    # Only rewrite file if we actually shortened and still have speech.
    if after_ms <= 0 or after_ms >= before_ms or not stages:
        return meta

    work_dir.mkdir(parents=True, exist_ok=True)
    from engines.pipeline_integrity.audio_identity import (
        allocate_tts_path,
        ensure_segment_uuid,
    )

    suid = ensure_segment_uuid(seg)
    out = allocate_tts_path(
        work_dir,
        segment_uuid=suid,
        ext=".wav",
        purpose="pause",
    )
    try:
        audio.export(str(out), format="wav")
    except Exception as exc:
        logger.debug("pause engine export failed: %s", exc)
        return meta

    seg["file"] = str(out)
    seg["tts_file_path"] = str(out)
    seg["playback_duration"] = after_ms
    seg["tts_ms"] = after_ms
    seg["actual_duration_ms"] = after_ms
    try:
        from engines.pipeline_integrity.audio_identity import bind_segment_audio

        bind_segment_audio(seg, out.name, duration_ms=after_ms)
        # Keep absolute path for local pause workdir resolution
        seg["file"] = str(out)
        seg["tts_file_path"] = str(out)
    except Exception:
        pass
    seg["pause_engine"] = {
        "before_ms": before_ms,
        "after_ms": after_ms,
        "saved_ms": before_ms - after_ms,
        "stages": stages,
        "slot_ms": slot_ms,
    }
    meta["applied"] = True
    return meta


def _segment_text(seg: dict) -> str:
    return str(
        seg.get("plain_text")
        or seg.get("translation_text")
        or seg.get("text")
        or ""
    ).strip()


def _needs_rewrite(budget: TimingBudget) -> tuple[bool, str]:
    if budget.status == "overflow" or budget.overflow > OVERFLOW_THRESHOLD_MS:
        return True, "duration_overflow"
    if budget.status == "underflow" or budget.underflow > UNDERFLOW_THRESHOLD_MS:
        return True, "duration_underflow"
    return False, ""


def _slot_delta_ms(budget: TimingBudget) -> int:
    """tts_ms - slot_ms: >0 overflow, <0 underflow."""
    return int(budget.measured_duration or 0) - int(budget.slot_duration or 0)


def _needs_stage19b_text_fit(budget: TimingBudget) -> bool:
    return abs(_slot_delta_ms(budget)) > TEXT_FIT_DELTA_MS


def large_overflow_needs_split(*, overflow_ms: int, slot_ms: int) -> bool:
    """Stage 19c §C: overflow > max(3000, 0.25×slot) → split by sentences."""
    slot = max(0, int(slot_ms or 0))
    ov = max(0, int(overflow_ms or 0))
    if ov <= TEXT_FIT_DELTA_MS:
        return False
    return ov > max(OVERFLOW_SPLIT_ABS_MS, int(slot * OVERFLOW_SPLIT_RATIO))


def _allocate_times_speech_expanded(
    chunks: list[str],
    start_ms: int,
    lang: str = "uk",
) -> list[tuple[str, int, int]]:
    """Stage 19i: child slots proportional to speech/text (not squeezed into parent).

    Duration follows predicted TTS so fill≈1.0; oversized children re-split
    via needs_post_restore_split. Floor at MIN_CHILD_SLOT_MS (2.2s).
    """
    from engines.text_slot_fit import (
        EXPAND_AIM_RATIO,
        MIN_CHILD_SLOT_MS,
        estimate_tts_ms,
    )

    out: list[tuple[str, int, int]] = []
    cursor = int(start_ms)
    preds = [max(1, int(estimate_tts_ms(c, lang) or 0)) for c in chunks]
    lens = [max(1, len(str(c or "").replace(" ", ""))) for c in chunks]
    total_len = sum(lens) or 1
    total_speech = sum(
        max(MIN_CHILD_SLOT_MS, int(p / max(float(EXPAND_AIM_RATIO), 0.5)))
        for p in preds
    ) or 1
    even = max(lens) <= min(lens) * 1.25 if lens else True
    for i, chunk in enumerate(chunks):
        if even:
            dur = max(
                MIN_CHILD_SLOT_MS,
                int(preds[i] / max(float(EXPAND_AIM_RATIO), 0.5)),
            )
        else:
            dur = max(
                MIN_CHILD_SLOT_MS,
                int(total_speech * (lens[i] / float(total_len))),
            )
        out.append((str(chunk), cursor, cursor + dur))
        cursor += dur
    return out


def segment_needs_stage19e_split(
    seg: dict,
    *,
    slot_ms: int,
    measured_ms: int = 0,
    lang: str = "uk",
) -> bool:
    """True when restored/giant text must be split before keeping one slot."""
    from engines.text_slot_fit import (
        MAX_CHILD_FILL,
        MAX_FILL_RATIO_AFTER_RESTORE,
        estimate_tts_ms,
        should_force_split,
    )

    text = _segment_text(seg)
    if not text or _text_looks_english(text):
        return False
    slot = max(0, int(slot_ms or seg.get("slot_ms") or 0))
    measured = max(0, int(measured_ms or 0))
    pred = int(estimate_tts_ms(text, lang) or 0)
    fill_pred = (pred / float(slot)) if slot > 0 and pred > 0 else 0.0
    fill_meas = (measured / float(slot)) if slot > 0 and measured > 0 else 0.0
    overfilled = (
        fill_pred > float(MAX_CHILD_FILL)
        or fill_meas > float(MAX_CHILD_FILL)
        or fill_pred > float(MAX_FILL_RATIO_AFTER_RESTORE)
        or fill_meas > float(MAX_FILL_RATIO_AFTER_RESTORE)
    )
    depth = int(
        seg.get("stage19g_split_depth")
        or seg.get("stage19f_split_depth")
        or 0
    )
    # Stage 19g: allow re-split of already-split children that are still overfilled.
    if seg.get("stage19e_split_done") or seg.get("stage19c_split_done"):
        if overfilled and depth < MAX_STAGE19G_SPLIT_DEPTH:
            return True
        return False
    if seg.get("needs_post_restore_split"):
        return True
    if should_force_split(text, slot, lang, predicted_ms=pred):
        return True
    return bool(overfilled)


def lock_text_fit_algorithm_reason(seg: dict, reason: str) -> None:
    """Stamp TextSlotFit* and prevent AudioStrategyNoTextRewrite overwrite."""
    r = str(reason or "").strip()
    if not r:
        return
    seg["algorithm_reason"] = r
    seg["text_adaptation_reason"] = r
    seg["algorithm_reason_locked"] = True
    seg["_algorithm_reason_locked"] = True


def _merge_short_chunk_spans(
    allocated: list[tuple[str, int, int]],
    *,
    min_ms: int = MIN_SPLIT_CHILD_MS,
) -> list[tuple[str, int, int]]:
    """Merge children shorter than min_ms back into neighbors."""
    if not allocated:
        return allocated
    out: list[tuple[str, int, int]] = []
    for text, start, end in allocated:
        dur = max(0, end - start)
        if out and dur < min_ms:
            prev_t, prev_s, _prev_e = out[-1]
            out[-1] = (f"{prev_t} {text}".strip(), prev_s, end)
        elif dur < min_ms and not out:
            out.append((text, start, end))
        else:
            out.append((text, start, end))
    # If first was short and we have 2+, merge forward.
    if len(out) >= 2 and (out[0][2] - out[0][1]) < min_ms:
        t0, s0, _e0 = out[0]
        t1, _s1, e1 = out[1]
        out = [(f"{t0} {t1}".strip(), s0, e1)] + out[2:]
    return out


def _pack_text_chunks(chunks: list[str], max_n: int) -> list[str]:
    """Pack many sentence chunks into at most max_n groups (by char weight)."""
    parts = [c.strip() for c in chunks if str(c or "").strip()]
    if len(parts) <= max_n:
        return parts
    weights = [max(1, len(c)) for c in parts]
    total = sum(weights) or 1
    target = total / float(max_n)
    packed: list[str] = []
    buf: list[str] = []
    wbuf = 0
    for c, w in zip(parts, weights):
        if buf and (wbuf + w) > target and len(packed) < max_n - 1:
            packed.append(" ".join(buf).strip())
            buf, wbuf = [c], w
        else:
            buf.append(c)
            wbuf += w
    if buf:
        packed.append(" ".join(buf).strip())
    return [p for p in packed if p]


def _text_looks_english(text: str) -> bool:
    """True when text is predominantly Latin (unsafe as UK Final/TTS)."""
    t = str(text or "").strip()
    if not t:
        return False
    letters = [ch for ch in t if ch.isalpha()]
    if not letters:
        return False
    latin = sum(1 for ch in letters if ("a" <= ch.lower() <= "z"))
    cyr = sum(1 for ch in letters if "\u0400" <= ch <= "\u04FF")
    if cyr >= max(3, int(len(letters) * 0.25)):
        return False
    return latin >= int(len(letters) * 0.70)


def _split_tgt_by_sources(tgt: str, src_parts: list[str]) -> list[str] | None:
    """Align Final UA to N source parts — never return EN source as Final."""
    parts = [p.strip() for p in src_parts if str(p or "").strip()]
    tgt = " ".join(str(tgt or "").split()).strip()
    if not tgt or len(parts) < 2:
        return None
    if len(parts) == 2:
        try:
            from engines.translation_segment_parity import split_translation_by_sources

            left, right = split_translation_by_sources(tgt, parts)
            if left and right and not _text_looks_english(left + " " + right):
                return [left.strip(), right.strip()]
        except Exception:
            pass
    # Proportional word split of Final (UA) — last resort, still not EN.
    words = tgt.split()
    if len(words) < len(parts):
        return None
    weights = [max(1, len(p.split())) for p in parts]
    total_w = sum(weights) or 1
    out: list[str] = []
    cursor = 0
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            piece = words[cursor:]
        else:
            n_take = max(1, int(round(len(words) * (w / total_w))))
            n_take = min(n_take, len(words) - cursor - (len(weights) - i - 1))
            piece = words[cursor : cursor + n_take]
            cursor += n_take
        chunk = " ".join(piece).strip()
        if not chunk or _text_looks_english(chunk):
            return None
        out.append(chunk)
    return out if len(out) == len(parts) else None


def rollback_stage19c_split(
    *,
    segments_data: list[dict],
    source_segments: list[str],
    timing_map: list,
    audits: list[dict] | None,
    idx: int,
    n_children: int,
    parent_backup: dict,
    parent_src: str,
    parent_timing: Any,
) -> None:
    """Undo a failed Stage 19c split that left children without TTS."""
    import copy

    n = max(1, int(n_children or 1))
    # Remove siblings idx+1 .. idx+n-1
    for _ in range(n - 1):
        if idx + 1 < len(segments_data):
            segments_data.pop(idx + 1)
        if idx + 1 < len(source_segments):
            source_segments.pop(idx + 1)
        if idx + 1 < len(timing_map):
            timing_map.pop(idx + 1)
    restored = copy.deepcopy(parent_backup)
    segments_data[idx] = restored
    if idx < len(source_segments):
        source_segments[idx] = parent_src
    else:
        source_segments.append(parent_src)
    if idx < len(timing_map):
        timing_map[idx] = parent_timing
    else:
        timing_map.append(parent_timing)
    if audits is not None:
        # Drop split audits in the child range; keep best-effort parent index.
        kept = [
            a
            for a in audits
            if not (
                idx < int(a.get("index", -1)) < idx + n
                and a.get("stage19c_split")
            )
        ]
        audits.clear()
        audits.extend(kept)
    logger.warning(
        "[Stage19c] rolled back overflow split at idx=%s (n_children=%s) — regen incomplete",
        idx,
        n,
    )


def try_stage19c_overflow_split(
    *,
    segments_data: list[dict],
    source_segments: list[str],
    timing_map: list,
    audits: list[dict] | None,
    idx: int,
    overflow_ms: int | None = None,
) -> bool:
    """Split one large-overflow segment into 2..4 sentence children (Stage 19c §C).

    Final/TTS text is always target-language (UA) — never EN source chunks.
    Works on Happy Path. Mutates lists in place.
    """
    import copy

    if idx < 0 or idx >= len(segments_data):
        return False
    seg = segments_data[idx]
    if seg.get("stage19c_split_done") or seg.get("adaptive_resegment_done"):
        return False

    if idx < len(timing_map):
        item = timing_map[idx]
        if isinstance(item, dict):
            start_ms = int(item.get("start") or item.get("start_ms") or 0)
            end_ms = int(item.get("end") or item.get("end_ms") or 0)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start_ms, end_ms = int(item[0]), int(item[1])
        else:
            start_ms = int(seg.get("start_ms") or 0)
            end_ms = start_ms + int(seg.get("slot_ms") or 0)
    else:
        start_ms = int(seg.get("start_ms") or 0)
        end_ms = start_ms + int(seg.get("slot_ms") or 0)
    slot_ms = max(0, end_ms - start_ms)
    tts_ms = int(
        seg.get("playback_duration")
        or seg.get("tts_ms")
        or seg.get("actual_duration_ms")
        or 0
    )
    ov = max(0, int(overflow_ms if overflow_ms is not None else (tts_ms - slot_ms)))
    if not large_overflow_needs_split(overflow_ms=ov, slot_ms=slot_ms):
        return False

    src = ""
    if idx < len(source_segments):
        src = str(source_segments[idx] or "").strip()
    tgt = _segment_text(seg)
    if not tgt:
        return False
    # Refuse to split when Final is already EN (would create EN TTS orphans).
    if _text_looks_english(tgt):
        logger.warning(
            "[Stage19c] refuse split seg#%d — Final looks English", idx
        )
        return False

    try:
        from engines.adaptive_segmentation.core import (
            _allocate_times,
            _safe_split_chunks,
        )
    except Exception as exc:
        logger.debug("stage19c split imports failed: %s", exc)
        return False

    # Prefer Final (UA) sentence cuts — production order.
    tgt_parts = _safe_split_chunks(tgt)
    src_parts = _safe_split_chunks(src) if src else []
    if len(tgt_parts) >= 2:
        tgt_chunks = _pack_text_chunks(tgt_parts, MAX_STAGE19C_SPLIT_CHILDREN)
        # Align source to same arity.
        if len(src_parts) >= 2:
            src_chunks = _pack_text_chunks(src_parts, len(tgt_chunks))
            if len(src_chunks) != len(tgt_chunks):
                src_chunks = _pack_text_chunks(
                    src_parts if src_parts else [src or ""], len(tgt_chunks)
                )
        else:
            src_chunks = _pack_text_chunks([src] if src else tgt_chunks, len(tgt_chunks))
    elif len(src_parts) >= 2:
        # Source has sentences; Final does not — 2-way max via parity split.
        src_chunks = _pack_text_chunks(
            [src_parts[0], " ".join(src_parts[1:]).strip()], 2
        )
        aligned = _split_tgt_by_sources(tgt, src_chunks)
        if not aligned:
            return False
        tgt_chunks = aligned
    else:
        return False

    if len(tgt_chunks) < 2:
        return False
    if any(_text_looks_english(t) for t in tgt_chunks):
        logger.warning(
            "[Stage19c] refuse split seg#%d — child Final looks English", idx
        )
        return False
    if len(src_chunks) != len(tgt_chunks):
        src_chunks = _pack_text_chunks(
            src_parts if len(src_parts) >= 2 else ([src] if src else tgt_chunks),
            len(tgt_chunks),
        )
        if len(src_chunks) != len(tgt_chunks):
            src_chunks = list(tgt_chunks)  # timing-only; source hint degraded

    # Allocate times from UA chunk lengths (speech estimate proxy).
    allocated = _allocate_times(tgt_chunks, start_ms, end_ms)
    allocated = _merge_short_chunk_spans(allocated, min_ms=MIN_SPLIT_CHILD_MS)
    if len(allocated) < 2:
        return False
    # Re-sync texts after merge.
    n = len(allocated)
    if n != len(tgt_chunks):
        tgt_chunks = [c[0] for c in allocated]
        src_chunks = _pack_text_chunks(
            src_parts if len(src_parts) >= 2 else ([src] if src else tgt_chunks),
            n,
        )
        if len(src_chunks) != n:
            src_chunks = list(tgt_chunks)

    # Build child segment dicts.
    children: list[dict] = []
    for i, ((text, st, en), src_t, tgt_t) in enumerate(
        zip(allocated, src_chunks, tgt_chunks)
    ):
        child = copy.deepcopy(seg) if i > 0 else seg
        clear_keys = (
            "file",
            "tts_file_path",
            "tts_ms",
            "playback_duration",
            "actual_duration_ms",
            "tts_timing",
            "post_tts_retry",
            "text_adaptation_trace",
            "fitted_file",
            "first_tts_duration_ms",
            "final_tts_duration_ms",
        )
        for key in clear_keys:
            child.pop(key, None)
        if i > 0:
            try:
                from engines.pipeline_integrity.segment import new_segment_id

                parent_sid = str(seg.get("segment_id") or "").strip()
                new_sid = new_segment_id()
                child["segment_id"] = new_sid
                child["segment_uuid"] = new_sid
                if parent_sid:
                    child["reissued_from"] = [parent_sid]
                    child["split_from_segment_id"] = parent_sid
            except Exception:
                pass

        final_txt = str(tgt_t or text or "").strip()
        if not final_txt or _text_looks_english(final_txt):
            logger.warning(
                "[Stage19c] abort split seg#%d — empty/EN child Final", idx
            )
            return False
        child["plain_text"] = final_txt
        child["text"] = final_txt
        child["final_text"] = final_txt
        child["translation_text"] = final_txt
        child["final_tts_text"] = final_txt
        child["start_ms"] = int(st)
        child["end_ms"] = int(en)
        child["slot_ms"] = max(1, int(en) - int(st))
        child["stage19c_split_done"] = True
        child["split_executed"] = True
        child["adaptive_resegment_done"] = True
        child["strategy"] = "split"
        lock_text_fit_algorithm_reason(child, "TextSlotSplit")
        child["expansion_strategy"] = "split"
        child["rule_rewrite_used"] = True
        child["adaptation_stages"] = list(child.get("adaptation_stages") or []) + [
            "stage19c_overflow_split"
        ]
        child["stage19c"] = {
            "split_parent_idx": idx,
            "split_child": i,
            "split_children": n,
            "delta_before": ov,
            "overflow_ms_before": ov,
        }
        children.append(child)

    # Insert siblings after idx
    for i, child in enumerate(children):
        if i == 0:
            segments_data[idx] = child
            continue
        segments_data.insert(idx + i, child)

    # Source + timing
    for i, (_text, st, en) in enumerate(allocated):
        src_t = src_chunks[i] if i < len(src_chunks) else ""
        timing_entry = {"start": int(st), "end": int(en)}
        if i == 0:
            if idx < len(source_segments):
                source_segments[idx] = src_t
            else:
                source_segments.append(src_t)
            if idx < len(timing_map):
                timing_map[idx] = timing_entry
            else:
                timing_map.append(timing_entry)
        else:
            source_segments.insert(idx + i, src_t)
            timing_map.insert(idx + i, timing_entry)

    if audits is not None:
        audit_by = {int(a.get("index", -1)): a for a in audits}
        parent_audit = dict(audit_by.get(idx) or {"index": idx})
        rebuilt: list[dict[str, Any]] = []
        for a in audits:
            ai = int(a.get("index", -1))
            if ai == idx:
                continue
            if ai > idx:
                a = dict(a)
                a["index"] = ai + (n - 1)
            rebuilt.append(a)
        for i in range(n):
            aa = dict(parent_audit)
            aa["index"] = idx + i
            aa["whisper_text"] = src_chunks[i] if i < len(src_chunks) else ""
            aa["final_text"] = tgt_chunks[i] if i < len(tgt_chunks) else ""
            aa["stage19c_split"] = True
            rebuilt.append(aa)
        rebuilt.sort(key=lambda x: int(x.get("index", 0)))
        audits.clear()
        audits.extend(rebuilt)

    logger.info(
        "[Stage19c] overflow split seg#%d ov=%dms slot=%dms → %d children (UA Final)",
        idx,
        ov,
        slot_ms,
        n,
    )
    return True


def _scope_child_text_anchors(child: dict, unique_chunk: str) -> None:
    """Stage 19h: bind all text/raw anchors to the unique child chunk.

    Deepcopy from parent otherwise leaves full parent raw_translation, and
    expand/anti-truncate restores that full text onto every child.
    """
    chunk = " ".join(str(unique_chunk or "").split()).strip()
    if not chunk:
        return
    for key in (
        "plain_text",
        "text",
        "final_text",
        "translation_text",
        "translated_text",
        "final_tts_text",
        "tts_text",
        "naturalized_text",
        "raw_translation",
        "raw_mt",
        "semantic_engine_text",
        "semantic_text",
        "mt_text",
    ):
        child[key] = chunk
    child["expand_executed"] = False
    child["text_changed"] = True


def _stage21_watchdog_heartbeat(task_id: str | None, **fields: Any) -> None:
    """Keep PIPELINE_STALLED from firing during slow tts_uk / split regen."""
    if not task_id:
        return
    try:
        from engines.pipeline_watchdog import get_pipeline_watchdog

        wd = get_pipeline_watchdog(str(task_id))
        if wd is not None:
            wd.heartbeat(**fields)
    except Exception:
        pass


def try_stage19e_post_restore_split(
    *,
    segments_data: list[dict],
    source_segments: list[str],
    timing_map: list,
    audits: list[dict] | None,
    idx: int,
    lang: str = "uk",
) -> bool:
    """Stage 21: overflow>350ms → aggressive clean split + independent re-TTS.

    Uses force_split_until_fit so each child predicted fill ≤ 1.12
    and lands near production 2.2–7.5s chunks. Children never inherit
    parent text/duration; each needs its own TTS. Garbage expand forbidden.
    """
    import copy

    from engines.text_slot_fit import (
        MAX_CHILD_FILL,
        MAX_SPLIT_CHILDREN,
        OVERFLOW_FORCE_SPLIT_MS,
        assert_clean_split_chunks,
        assert_unique_split_chunks,
        char_budget,
        cps_over_budget,
        estimate_tts_ms,
        force_split_until_fit,
        is_clean_utterance,
        is_garbage_expand,
        should_force_split,
        soft_pad_count,
        strip_garbage_expand_phrases,
    )

    if idx < 0 or idx >= len(segments_data):
        return False
    seg = segments_data[idx]
    depth = int(
        seg.get("stage21_split_depth")
        or seg.get("stage19j_split_depth")
        or seg.get("stage19i_split_depth")
        or seg.get("stage19h_split_depth")
        or seg.get("stage19g_split_depth")
        or seg.get("stage19f_split_depth")
        or 0
    )
    if depth >= MAX_STAGE21_SPLIT_DEPTH:
        return False

    if idx < len(timing_map):
        item = timing_map[idx]
        if isinstance(item, dict):
            start_ms = int(item.get("start") or item.get("start_ms") or 0)
            end_ms = int(item.get("end") or item.get("end_ms") or 0)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start_ms, end_ms = int(item[0]), int(item[1])
        else:
            start_ms = int(seg.get("start_ms") or 0)
            end_ms = start_ms + int(seg.get("slot_ms") or 0)
    else:
        start_ms = int(seg.get("start_ms") or 0)
        end_ms = start_ms + int(seg.get("slot_ms") or 0)
    slot_ms = max(0, end_ms - start_ms)
    tgt = _segment_text(seg)
    parent_text = " ".join(str(tgt or "").split()).strip()
    if not parent_text or _text_looks_english(parent_text):
        return False
    measured_ms = int(
        seg.get("playback_duration")
        or seg.get("tts_ms")
        or seg.get("actual_duration_ms")
        or 0
    )
    fill_ratio_now = (
        (measured_ms / float(slot_ms)) if slot_ms > 0 and measured_ms > 0 else 0.0
    )
    overflow_ms_now = (
        int(measured_ms - slot_ms) if slot_ms > 0 and measured_ms > 0 else 0
    )
    parent_text = strip_garbage_expand_phrases(parent_text)
    if not (
        seg.get("needs_post_restore_split")
        or should_force_split(
            parent_text, slot_ms, lang, measured_ms=measured_ms or None
        )
        or fill_ratio_now > MAX_CHILD_FILL
        or overflow_ms_now > OVERFLOW_FORCE_SPLIT_MS
        or (slot_ms > 0 and cps_over_budget(parent_text, slot_ms))
        or segment_needs_stage19e_split(
            seg,
            slot_ms=slot_ms,
            measured_ms=measured_ms,
            lang=lang,
        )
    ):
        return False

    src = ""
    if idx < len(source_segments):
        src = str(source_segments[idx] or "").strip()

    # Aim packs at production child size (2.2–7.5s), not the whole parent window.
    pack_slot = slot_ms if slot_ms > 0 else 4000
    if (
        measured_ms > pack_slot * MAX_CHILD_FILL
        or overflow_ms_now > OVERFLOW_FORCE_SPLIT_MS
        or cps_over_budget(parent_text, pack_slot)
    ):
        pack_slot = max(2200, min(7500, int(pack_slot * 0.92) if pack_slot else 4000))
    tgt_chunks = force_split_until_fit(
        parent_text,
        pack_slot,
        lang,
        max_children=min(MAX_SPLIT_CHILDREN, MAX_STAGE21_SPLIT_CHILDREN),
        depth=0,
    )
    if len(tgt_chunks) < 2:
        return False
    unique_text_ok = assert_unique_split_chunks(parent_text, tgt_chunks)
    clean_split_ok = assert_clean_split_chunks(tgt_chunks) and all(
        not is_garbage_expand(c) for c in tgt_chunks
    )
    if not unique_text_ok or not clean_split_ok:
        logger.warning(
            "[Stage21] refuse split seg#%d — unique_ok=%s clean_ok=%s",
            idx,
            unique_text_ok,
            clean_split_ok,
        )
        bad = {
            "split_parent_idx": idx,
            "split_children": len(tgt_chunks),
            "unique_text_ok": bool(unique_text_ok),
            "clean_split_ok": bool(clean_split_ok),
            "force_split_executed": False,
            "stage21_split_depth": depth,
            "stage19j_split_depth": depth,
            "stage19i_split_depth": depth,
            "stage19h_split_depth": depth,
            "char_budget": char_budget(slot_ms) if slot_ms else 0,
            "final_status": "overflow_unresolved",
            "fill_ratio": round(fill_ratio_now, 4),
            "overflow_ms": overflow_ms_now,
            "delta": overflow_ms_now,
            "expand_executed": False,
            "text_changed": False,
            "soft_pad_count": soft_pad_count(parent_text),
            "garbage_expand_blocked": 0,
        }
        seg["stage19h"] = {**(seg.get("stage19h") or {}), **bad}
        seg["stage19i"] = {**(seg.get("stage19i") or {}), **bad}
        seg["stage19j"] = {**(seg.get("stage19j") or {}), **bad}
        seg["stage21"] = {**(seg.get("stage21") or {}), **bad}
        seg["unique_text_ok"] = bool(unique_text_ok)
        seg["clean_split_ok"] = bool(clean_split_ok)
        return False
    if any(_text_looks_english(t) for t in tgt_chunks):
        logger.warning(
            "[Stage21] refuse split seg#%d — child Final looks English", idx
        )
        return False
    assert all(
        " ".join(str(c).split()).strip() != parent_text for c in tgt_chunks
    ), "Stage21: child must not equal parent text"
    assert all(is_clean_utterance(c) for c in tgt_chunks), (
        "Stage21: child must be a clean utterance"
    )
    assert all(not is_garbage_expand(c) for c in tgt_chunks), (
        "Stage21: child must not contain garbage expand"
    )

    src_parts: list[str] = []
    try:
        from engines.adaptive_segmentation.core import _safe_split_chunks

        src_parts = _safe_split_chunks(src) if src else []
    except Exception:
        src_parts = []
    if len(src_parts) >= 2:
        src_chunks = _pack_text_chunks(src_parts, len(tgt_chunks))
        if len(src_chunks) != len(tgt_chunks):
            src_chunks = _pack_text_chunks(
                src_parts if src_parts else [src or ""], len(tgt_chunks)
            )
    else:
        src_chunks = _pack_text_chunks([src] if src else tgt_chunks, len(tgt_chunks))
    if len(src_chunks) != len(tgt_chunks):
        src_chunks = list(tgt_chunks)

    allocated = _allocate_times_speech_expanded(tgt_chunks, start_ms, lang)
    if len(allocated) < 2:
        return False
    n = len(allocated)
    if n != len(tgt_chunks):
        tgt_chunks = [c[0] for c in allocated]
        if not assert_unique_split_chunks(parent_text, tgt_chunks):
            logger.warning(
                "[Stage19i] refuse split seg#%d — allocated chunks not unique vs parent",
                idx,
            )
            return False
        src_chunks = _pack_text_chunks(
            src_parts if len(src_parts) >= 2 else ([src] if src else tgt_chunks),
            n,
        )
        if len(src_chunks) != n:
            src_chunks = list(tgt_chunks)

    parent_end = end_ms
    new_end = int(allocated[-1][2])
    shift_ms = max(0, new_end - parent_end)
    child_depth = depth + 1

    children: list[dict] = []
    for i, ((text, st, en), src_t, tgt_t) in enumerate(
        zip(allocated, src_chunks, tgt_chunks)
    ):
        child = copy.deepcopy(seg) if i > 0 else seg
        clear_keys = (
            "file",
            "tts_file_path",
            "tts_ms",
            "playback_duration",
            "actual_duration_ms",
            "measured_duration",
            "tts_duration",
            "tts_timing",
            "post_tts_retry",
            "text_adaptation_trace",
            "fitted_file",
            "first_tts_duration_ms",
            "final_tts_duration_ms",
            "needs_post_restore_split",
            "timing_budget",
            "closed_loop",
        )
        for key in clear_keys:
            child.pop(key, None)
        # Forbidden: never copy parent duration onto children.
        for forbidden in (
            "tts_ms",
            "playback_duration",
            "actual_duration_ms",
            "measured_duration",
            "tts_duration",
            "first_tts_duration_ms",
            "final_tts_duration_ms",
        ):
            if forbidden in child:
                child.pop(forbidden, None)
        if i > 0:
            try:
                from engines.pipeline_integrity.segment import new_segment_id

                parent_sid = str(seg.get("segment_id") or "").strip()
                new_sid = new_segment_id()
                child["segment_id"] = new_sid
                child["segment_uuid"] = new_sid
                if parent_sid:
                    child["reissued_from"] = [parent_sid]
                    child["split_from_segment_id"] = parent_sid
            except Exception:
                pass

        final_txt = " ".join(str(tgt_t or text or "").split()).strip()
        if (
            not final_txt
            or _text_looks_english(final_txt)
            or final_txt == parent_text
        ):
            logger.warning(
                "[Stage19i] abort split seg#%d — empty/EN/parent-equal child Final",
                idx,
            )
            return False
        child_slot = max(1, int(en) - int(st))
        child_pred = int(estimate_tts_ms(final_txt, lang) or 0)
        child_fill = child_pred / float(child_slot) if child_slot else 0.0
        still_over = child_fill > float(MAX_CHILD_FILL) or cps_over_budget(
            final_txt, child_slot
        )
        # Bind ALL text/raw anchors to unique chunk (Stage 19h/19i root-cause fix).
        _scope_child_text_anchors(child, final_txt)
        child["start_ms"] = int(st)
        child["end_ms"] = int(en)
        child["slot_ms"] = child_slot
        child["stage19e_split_done"] = True
        # Allow Stage 19i re-split when this child is still overfilled.
        child["stage19c_split_done"] = not still_over
        child["split_executed"] = True
        child["post_restore_split"] = True
        child["stage19f_split_depth"] = child_depth
        child["stage19g_split_depth"] = child_depth
        child["stage19h_split_depth"] = child_depth
        child["stage19i_split_depth"] = child_depth
        child["stage19j_split_depth"] = child_depth
        child["stage21_split_depth"] = child_depth
        child["needs_post_restore_split"] = bool(
            still_over and child_depth < MAX_STAGE21_SPLIT_DEPTH
        )
        child["adaptive_resegment_done"] = True
        child["strategy"] = "split"
        child["fill_ratio"] = round(child_fill, 4)
        child["unique_text_ok"] = True
        child["clean_split_ok"] = True
        child["needs_re_tts"] = True  # independent child TTS — never inherit parent
        if still_over and child_depth >= MAX_STAGE21_SPLIT_DEPTH:
            child["final_status"] = "overflow_unresolved"
        lock_text_fit_algorithm_reason(child, "TextSlotFitSplit")
        child["expansion_strategy"] = "split"
        child["rule_rewrite_used"] = True
        child["adaptation_stages"] = list(child.get("adaptation_stages") or []) + [
            "stage21_force_split"
        ]
        final_status = (
            "overflow_unresolved"
            if still_over and child_depth >= MAX_STAGE21_SPLIT_DEPTH
            else "stage21_partial"
        )
        child_budget = char_budget(child_slot)
        meta = {
            "split_parent_idx": idx,
            "split_child": i,
            "split_children": n,
            "post_restore_split": True,
            "split_executed": True,
            "force_split_executed": True,
            "expand_executed": False,
            "text_changed": True,
            "shorten_executed": False,
            "algorithm_reason": "TextSlotFitSplit",
            "fill_ratio": round(child_fill, 4),
            "overflow_ms": 0,
            "stage19f_split_depth": child_depth,
            "stage19g_split_depth": child_depth,
            "stage19h_split_depth": child_depth,
            "stage19i_split_depth": child_depth,
            "stage19j_split_depth": child_depth,
            "stage21_split_depth": child_depth,
            "unique_text_ok": True,
            "clean_split_ok": True,
            "garbage_expand_blocked": 0,
            "char_budget": child_budget,
            "estimated_cps": 0.0,
            "soft_pad_count": soft_pad_count(final_txt),
            "atempo_ratio": None,
            "delta": 0,  # unknown until independent measure
            "final_status": final_status,
        }
        child["stage19e"] = dict(meta)
        child["stage19f"] = dict(meta)
        child["stage19g"] = dict(meta)
        child["stage19h"] = dict(meta)
        child["stage19i"] = dict(meta)
        child["stage19j"] = dict(meta)
        child["stage21"] = dict(meta)
        children.append(child)

    for i, child in enumerate(children):
        if i == 0:
            segments_data[idx] = child
            continue
        segments_data.insert(idx + i, child)

    for i, (_text, st, en) in enumerate(allocated):
        src_t = src_chunks[i] if i < len(src_chunks) else ""
        timing_entry = {"start": int(st), "end": int(en)}
        if i == 0:
            if idx < len(source_segments):
                source_segments[idx] = src_t
            else:
                source_segments.append(src_t)
            if idx < len(timing_map):
                timing_map[idx] = timing_entry
            else:
                timing_map.append(timing_entry)
        else:
            source_segments.insert(idx + i, src_t)
            timing_map.insert(idx + i, timing_entry)

    # Shift subsequent slots so expanded children do not pile into one tiny window.
    if shift_ms > 0:
        for j in range(idx + n, len(timing_map)):
            item = timing_map[j]
            if isinstance(item, dict):
                timing_map[j] = {
                    **item,
                    "start": int(item.get("start") or item.get("start_ms") or 0)
                    + shift_ms,
                    "end": int(item.get("end") or item.get("end_ms") or 0) + shift_ms,
                }
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                timing_map[j] = [int(item[0]) + shift_ms, int(item[1]) + shift_ms]
            if j < len(segments_data):
                sj = segments_data[j]
                sj["start_ms"] = int(sj.get("start_ms") or 0) + shift_ms
                sj["end_ms"] = int(sj.get("end_ms") or 0) + shift_ms

    if audits is not None:
        audit_by = {int(a.get("index", -1)): a for a in audits}
        parent_audit = dict(audit_by.get(idx) or {"index": idx})
        rebuilt: list[dict[str, Any]] = []
        for a in audits:
            ai = int(a.get("index", -1))
            if ai == idx:
                continue
            if ai > idx:
                a = dict(a)
                a["index"] = ai + (n - 1)
            rebuilt.append(a)
        for i in range(n):
            aa = dict(parent_audit)
            aa["index"] = idx + i
            aa["whisper_text"] = src_chunks[i] if i < len(src_chunks) else ""
            child_final = tgt_chunks[i] if i < len(tgt_chunks) else ""
            aa["final_text"] = child_final
            # Scope audit raw anchors to unique child text (not parent blob).
            aa["raw_translation"] = child_final
            aa["raw_mt"] = child_final
            aa["stage19e_split"] = True
            aa["stage19f_split"] = True
            aa["stage19g_split"] = True
            aa["stage19h_split"] = True
            aa["stage19i_split"] = True
            aa["stage19j_split"] = True
            aa["post_restore_split"] = True
            aa["unique_text_ok"] = True
            aa["clean_split_ok"] = True
            rebuilt.append(aa)
        rebuilt.sort(key=lambda x: int(x.get("index", 0)))
        audits.clear()
        audits.extend(rebuilt)

    logger.info(
        "[Stage19j] post-restore split seg#%d slot=%dms → %d clean unique children "
        "(expanded end=%dms depth=%d clean_split_ok=True)",
        idx,
        slot_ms,
        n,
        new_end,
        child_depth,
    )
    return True


def _stage19b_algorithm_reason(fit: Any, *, text_changed: bool) -> str:
    action = str(getattr(fit, "action", "") or "")
    strategy = str(getattr(fit, "strategy", "") or "")
    if action in ("expand",) or strategy == "expand":
        return "TextSlotFitExpand"
    if action == "shorten" or strategy == "shorten":
        return "TextSlotFitShorten"
    if action in ("expand_then_slow", "atempo_slow", "atempo_prefer") or strategy in (
        "expand_then_slow",
        "atempo_slow",
        "shorten_then_fast",
    ):
        return "TextThenAtemo"
    if text_changed:
        return "TextSlotFitExpand" if "expand" in (action + strategy) else "TextSlotFitShorten"
    return "TextThenAtemo"


def _stage19d_sanitize_algorithm_reason(
    reason: str,
    *,
    expand_executed: bool,
    shorten_executed: bool,
    split_executed: bool,
    delta_ms: int,
    underflow_ms: int,
    overflow_ms: int,
) -> str:
    """Stage 19d/19f: forbid bare TextThenAtemo / false TextSlotFitExpand."""
    r = str(reason or "").strip() or "TextThenAtemo"
    abs_d = abs(int(delta_ms or 0))
    if split_executed:
        return "TextSlotFitSplit"
    if expand_executed:
        return "TextSlotFitExpand" if "Atemo" not in r else "TextThenAtemo"
    if shorten_executed:
        return "TextSlotFitShorten" if "Atemo" not in r else "TextThenAtemo"
    # Stage 19f: NEVER claim TextSlotFitExpand when expand did not change text.
    if r in ("TextSlotFitExpand",) or r.endswith("TextSlotFitExpand"):
        if int(underflow_ms or 0) > TEXT_FIT_DELTA_MS:
            return "dead_air_risk"
        r = "TextThenAtemo"
    if abs_d <= TEXT_FIT_DELTA_MS:
        return r if r.startswith("Text") else "TextThenAtemo"
    # Large Δ without text rewrite — do not claim TextThenAtemo as success.
    if r in ("TextThenAtemo",) or r.endswith("TextThenAtemo"):
        if int(underflow_ms or 0) > TEXT_FIT_DELTA_MS:
            return "dead_air_risk"
        if int(overflow_ms or 0) > TEXT_FIT_DELTA_MS:
            return "TextSlotFitShorten"
    return r


def assert_no_silent_truncate(
    seg: dict,
    *,
    slot_ms: int = 0,
    lang: str = "uk",
    source_hint: str = "",
) -> None:
    """Stage 19d §D: restore raw/semantic when Final silently lost >25% words.

    Raises NeedReTTS after restoring text so caller performs mandatory re-TTS.
    """
    from engines.text_slot_fit import (
        detect_silent_truncate,
        safe_shorten,
        semantic_anchor_text,
        should_force_split,
        strip_slot_pad_fillers,
        word_retention_ratio,
        MIN_WORD_RETENTION,
    )

    raw = semantic_anchor_text(seg)
    final = str(
        seg.get("final_tts_text")
        or seg.get("plain_text")
        or seg.get("text")
        or ""
    ).strip()
    if not detect_silent_truncate(
        final,
        raw,
        shorten_executed=bool(seg.get("shorten_executed")),
    ):
        # Still stamp retention for diagnostics.
        if raw and final:
            seg["retention_score"] = round(word_retention_ratio(raw, final), 4)
        return

    slot = int(slot_ms or seg.get("slot_ms") or 0)
    restored, reasons, _tr = safe_shorten(
        raw, slot if slot > 0 else 200, lang, source_hint=source_hint
    )
    # If safe_shorten refused, keep full raw (meaning > timing).
    if not restored or word_retention_ratio(raw, restored) < MIN_WORD_RETENTION:
        restored = strip_slot_pad_fillers(raw)
        reasons = list(reasons or []) + ["restore_full_raw"]
    restored = strip_slot_pad_fillers(restored)
    if not restored or restored == final:
        seg["truncation_blocked"] = True
        seg["retention_score"] = round(word_retention_ratio(raw, final), 4)
        return

    seg["final_tts_text"] = restored
    seg["plain_text"] = restored
    seg["text"] = restored
    seg["translation_text"] = restored
    seg["final_text"] = restored
    seg["pre_tts_text"] = restored
    seg["truncation_blocked"] = True
    seg["rule_rewrite_used"] = True
    seg["retention_score"] = round(word_retention_ratio(raw, restored), 4)
    # Stage 19e: giant restore must force-split — do not claim shorten-only fit.
    force_split = should_force_split(restored, slot if slot > 0 else 0, lang)
    if force_split:
        seg["needs_post_restore_split"] = True
        seg["shorten_executed"] = bool(seg.get("shorten_executed"))
        lock_text_fit_algorithm_reason(seg, "TextSlotFitSplit")
        seg["rewrite_reason"] = "stage19e:anti_truncate_needs_split"
    else:
        seg["shorten_executed"] = True
        lock_text_fit_algorithm_reason(seg, "TextSlotFitShorten")
        seg["rewrite_reason"] = "stage19d:anti_truncate_restored"
    prev = dict(seg.get("stage19d") or {})
    seg["stage19d"] = {
        **prev,
        "truncation_blocked": True,
        "anti_truncate_reasons": list(reasons or [])
        + ["silent_truncate"]
        + (["needs_post_restore_split"] if force_split else []),
        "retention_score": seg["retention_score"],
        "needs_post_restore_split": bool(force_split),
    }
    raise NeedReTTS(
        "stage19e:anti_truncate_needs_split" if force_split else "anti_truncate_restored"
    )


def _stamp_stage19d_fields(
    seg: dict,
    *,
    budget: TimingBudget,
    algorithm_reason: str,
    expand_executed: bool,
    shorten_executed: bool,
    split_executed: bool = False,
) -> None:
    from engines.text_slot_fit import semantic_anchor_text, word_retention_ratio

    raw = semantic_anchor_text(seg)
    final = str(
        seg.get("final_tts_text")
        or seg.get("plain_text")
        or seg.get("text")
        or ""
    ).strip()
    retention = word_retention_ratio(raw, final) if raw and final else 1.0
    slot = max(1, int(budget.slot_duration or seg.get("slot_ms") or 1))
    tts = int(budget.measured_duration or seg.get("tts_ms") or 0)
    fill = round(tts / float(slot), 4) if tts > 0 else float(seg.get("fill_ratio") or 0)
    delta = int(budget.delta if budget.delta is not None else (tts - slot))
    reason = _stage19d_sanitize_algorithm_reason(
        algorithm_reason,
        expand_executed=expand_executed,
        shorten_executed=shorten_executed,
        split_executed=split_executed or bool(seg.get("split_executed")),
        delta_ms=delta,
        underflow_ms=int(budget.underflow or 0),
        overflow_ms=int(budget.overflow or 0),
    )
    lock_text_fit_algorithm_reason(seg, reason)
    seg["expand_executed"] = bool(expand_executed or seg.get("expand_executed"))
    seg["shorten_executed"] = bool(shorten_executed or seg.get("shorten_executed"))
    seg["split_executed"] = bool(split_executed or seg.get("split_executed") or seg.get("stage19c_split_done"))
    seg["truncation_blocked"] = bool(seg.get("truncation_blocked"))
    seg["retention_score"] = round(float(retention), 4)
    seg["fill_ratio"] = fill
    seg["rewrite_iterations"] = int(
        budget.rewrite_iterations or seg.get("rewrite_iterations") or 0
    )
    prev = dict(seg.get("stage19d") or {})
    seg["stage19d"] = {
        **prev,
        "algorithm_reason": reason,
        "expand_executed": bool(seg["expand_executed"]),
        "shorten_executed": bool(seg["shorten_executed"]),
        "split_executed": bool(seg["split_executed"]),
        "truncation_blocked": bool(seg["truncation_blocked"]),
        "retention_score": seg["retention_score"],
        "fill_ratio": fill,
        "rewrite_iterations": seg["rewrite_iterations"],
        "expansion_strategy": seg.get("expansion_strategy") or "none",
        "delta": delta,
        "final_status": budget.final_status,
    }
    _stamp_stage19e_fields(
        seg,
        budget=budget,
        algorithm_reason=reason,
        expand_executed=bool(seg["expand_executed"]),
        shorten_executed=bool(seg["shorten_executed"]),
        split_executed=bool(seg["split_executed"]),
    )


def _stamp_stage19e_fields(
    seg: dict,
    *,
    budget: TimingBudget,
    algorithm_reason: str,
    expand_executed: bool,
    shorten_executed: bool,
    split_executed: bool = False,
) -> None:
    """Stage 19e metadata — honest expand/split/fill after restore."""
    slot = max(1, int(budget.slot_duration or seg.get("slot_ms") or 1))
    tts = int(budget.measured_duration or seg.get("tts_ms") or 0)
    fill = round(tts / float(slot), 4) if tts > 0 else float(seg.get("fill_ratio") or 0)
    delta = int(budget.delta if budget.delta is not None else (tts - slot))
    reason = _stage19d_sanitize_algorithm_reason(
        algorithm_reason,
        expand_executed=expand_executed,
        shorten_executed=shorten_executed,
        split_executed=split_executed or bool(seg.get("split_executed")),
        delta_ms=delta,
        underflow_ms=int(budget.underflow or 0),
        overflow_ms=int(budget.overflow or 0),
    )
    final_status = str(budget.final_status or "pending")
    if abs(delta) > STAGE19D_HARD_DELTA_MS:
        if int(budget.underflow or 0) > STAGE19D_HARD_DELTA_MS:
            final_status = "dead_air_risk"
        elif int(budget.overflow or 0) > STAGE19D_HARD_DELTA_MS:
            final_status = "overflow_unresolved"
        elif final_status in ("ok", "pending"):
            final_status = "stage19e_partial"
    elif (
        int(budget.underflow or 0) > TEXT_FIT_DELTA_MS
        and not expand_executed
        and not split_executed
    ):
        final_status = "dead_air_risk"
    prev = dict(seg.get("stage19e") or {})
    split_children = int(
        prev.get("split_children")
        or (seg.get("stage19c") or {}).get("split_children")
        or 0
    )
    text_changed = bool(
        expand_executed
        or shorten_executed
        or split_executed
        or seg.get("rule_rewrite_used")
        or seg.get("text_changed")
    )
    # Stage 21: ok only inside fill/CPS/pad/unique/clean bounds — never mask overflow.
    from engines.text_slot_fit import (
        MAX_CPS_UK,
        MIN_CPS_UK,
        char_budget,
        estimated_cps,
        is_clean_utterance,
        is_garbage_expand,
        soft_pad_count,
        strip_garbage_expand_phrases,
    )

    seg_text = strip_garbage_expand_phrases(_segment_text(seg))
    # Persist scrubbed text so garbage never survives into final_tts_text.
    if seg_text and seg_text != _segment_text(seg):
        for _k in (
            "plain_text",
            "text",
            "final_text",
            "final_tts_text",
            "tts_text",
            "translation_text",
        ):
            if _k in seg:
                seg[_k] = seg_text
    unique_text_ok = seg.get("unique_text_ok")
    if unique_text_ok is None:
        unique_text_ok = (
            seg.get("stage21")
            or seg.get("stage19j")
            or seg.get("stage19i")
            or seg.get("stage19h")
            or {}
        ).get("unique_text_ok")
    if unique_text_ok is None:
        unique_text_ok = True
    unique_text_ok = bool(unique_text_ok)
    clean_split_ok = seg.get("clean_split_ok")
    if clean_split_ok is None:
        clean_split_ok = (seg.get("stage21") or seg.get("stage19j") or {}).get(
            "clean_split_ok"
        )
    if clean_split_ok is None:
        # Unsplit segments: clean if utterance itself is clean / no garbage.
        clean_split_ok = (
            (not is_garbage_expand(seg_text))
            and (is_clean_utterance(seg_text) or len(seg_text.split()) >= 3)
        )
    clean_split_ok = bool(clean_split_ok) and not is_garbage_expand(seg_text)
    garbage_blocked = int(
        seg.get("garbage_expand_blocked")
        or (seg.get("stage21") or {}).get("garbage_expand_blocked")
        or (seg.get("stage19j") or {}).get("garbage_expand_blocked")
        or 0
    )
    pad_n = soft_pad_count(seg_text)
    cps_now = estimated_cps(seg_text, tts) if tts > 0 else 0.0
    budget_chars = char_budget(slot)
    overflow_ms = int(budget.overflow or max(0, delta))
    force_split_executed = bool(
        split_executed
        or seg.get("split_executed")
        or (seg.get("stage21") or {}).get("force_split_executed")
        or (seg.get("stage19j") or {}).get("force_split_executed")
    )
    atempo_ratio = seg.get("atempo")
    try:
        atempo_f = float(atempo_ratio) if atempo_ratio is not None else None
    except (TypeError, ValueError):
        atempo_f = None
    atempo_ok = (
        atempo_f is not None
        and ATEMPO_MIN <= atempo_f <= ATEMPO_MAX
        and abs(delta) <= TEXT_FIT_DELTA_MS
    )
    cps_ok = (cps_now <= 0.0) or (MIN_CPS_UK <= cps_now <= MAX_CPS_UK)
    # Stage 22: final_status is driven by fill band (0.90–1.12). Absolute
    # underflow >350 only triggers expand attempts — not a dead_air stamp when
    # fill is already in band.
    hard_fail = final_status in ("failed_tts_regen", "failed_no_regen")
    if hard_fail:
        pass
    elif is_garbage_expand(seg_text):
        final_status = "stage23_partial"
        garbage_blocked = max(garbage_blocked, 1)
    elif not unique_text_ok:
        final_status = "overflow_unresolved"
    elif fill < STAGE23_OK_FILL_LO:
        final_status = "dead_air_risk"
    elif fill > STAGE23_OK_FILL_HI or overflow_ms > OVERFLOW_FORCE_SPLIT_MS:
        final_status = "overflow_unresolved"
    elif not clean_split_ok or pad_n > 2:
        final_status = "stage23_partial"
    elif STAGE23_OK_FILL_LO <= fill <= STAGE23_OK_FILL_HI:
        # In-band fill → ok (even if |delta| large or CPS off on a long slot).
        final_status = "ok"
    else:
        final_status = "stage23_partial"
    # Forbidden: never stamp ok with garbage / out-of-band fill / unclean split.
    if final_status == "ok" and (
        fill < STAGE23_OK_FILL_LO
        or fill > STAGE23_OK_FILL_HI
        or overflow_ms > OVERFLOW_FORCE_SPLIT_MS
        or pad_n > 2
        or not unique_text_ok
        or not clean_split_ok
        or is_garbage_expand(seg_text)
    ):
        if fill > STAGE23_OK_FILL_HI or not unique_text_ok or overflow_ms > OVERFLOW_FORCE_SPLIT_MS:
            final_status = "overflow_unresolved"
        elif fill < STAGE23_OK_FILL_LO:
            final_status = "dead_air_risk"
        else:
            final_status = "stage23_partial"
    split_depth = int(
        seg.get("stage21_split_depth")
        or seg.get("stage19j_split_depth")
        or seg.get("stage19i_split_depth")
        or seg.get("stage19h_split_depth")
        or seg.get("stage19g_split_depth")
        or seg.get("stage19f_split_depth")
        or prev.get("stage21_split_depth")
        or prev.get("stage19j_split_depth")
        or prev.get("stage19i_split_depth")
        or prev.get("stage19h_split_depth")
        or prev.get("stage19g_split_depth")
        or 0
    )
    meta19 = {
        **prev,
        "expand_executed": bool(expand_executed and text_changed),
        "text_changed": bool(text_changed),
        "shorten_executed": bool(shorten_executed or seg.get("shorten_executed")),
        "split_executed": bool(split_executed or seg.get("split_executed")),
        "force_split_executed": bool(force_split_executed),
        "post_restore_split": bool(
            seg.get("post_restore_split") or prev.get("post_restore_split")
        ),
        "truncation_blocked": bool(seg.get("truncation_blocked")),
        "fill_ratio": fill,
        "overflow_ms": overflow_ms,
        "retention_score": float(seg.get("retention_score") or 0),
        "delta": delta,
        "final_status": final_status,
        "algorithm_reason": reason,
        "split_children": split_children,
        "stage19g_split_depth": split_depth,
        "stage19h_split_depth": split_depth,
        "stage19i_split_depth": split_depth,
        "stage19j_split_depth": split_depth,
        "stage21_split_depth": split_depth,
        "unique_text_ok": unique_text_ok,
        "clean_split_ok": clean_split_ok,
        "garbage_expand_blocked": garbage_blocked,
        "char_budget": budget_chars,
        "estimated_cps": round(cps_now, 3),
        "soft_pad_count": pad_n,
        "atempo_ratio": round(atempo_f, 4) if atempo_f is not None else None,
        "split_parent_idx": prev.get("split_parent_idx", seg.get("split_parent_idx")),
        "split_child": prev.get("split_child", seg.get("split_child")),
    }
    # Stage 23 status names preferred when unresolved after fit.
    if final_status in (
        "stage19d_partial",
        "stage19f_partial",
        "stage19e_partial",
        "stage19g_partial",
        "stage19h_partial",
        "stage19i_partial",
        "stage19j_partial",
        "stage21_partial",
        "stage22_partial",
    ) and (
        abs(delta) > TEXT_FIT_DELTA_MS or seg.get("needs_post_restore_split")
    ):
        meta19["final_status"] = "stage23_partial"
        final_status = "stage23_partial"
        if budget.final_status in (
            "stage19d_partial",
            "stage19f_partial",
            "stage19e_partial",
            "stage19g_partial",
            "stage19h_partial",
            "stage19i_partial",
            "stage19j_partial",
            "stage21_partial",
            "stage22_partial",
        ):
            budget.final_status = "stage23_partial"
    if budget.final_status == "ok" and meta19["final_status"] != "ok":
        budget.final_status = meta19["final_status"]
    seg["stage19e"] = dict(meta19)
    prev_f = dict(seg.get("stage19f") or {})
    seg["stage19f"] = {**prev_f, **meta19}
    prev_g = dict(seg.get("stage19g") or {})
    seg["stage19g"] = {**prev_g, **meta19}
    prev_h = dict(seg.get("stage19h") or {})
    seg["stage19h"] = {**prev_h, **meta19}
    prev_i = dict(seg.get("stage19i") or {})
    seg["stage19i"] = {**prev_i, **meta19}
    prev_j = dict(seg.get("stage19j") or {})
    seg["stage19j"] = {**prev_j, **meta19}
    prev_21 = dict(seg.get("stage21") or {})
    seg["stage21"] = {**prev_21, **meta19}
    try:
        from engines.tts_backends import resolve_mykyta_controls

        ctrl = resolve_mykyta_controls(
            {
                "rate": seg.get("tts_rate"),
                "pitch": seg.get("tts_pitch"),
                "volume": seg.get("tts_volume"),
                "length_scale": seg.get("tts_length_scale"),
            }
        )
    except Exception:
        ctrl = {
            "rate": float(seg.get("tts_rate") or 1.0),
            "pitch": float(seg.get("tts_pitch") or 0.0),
            "volume": float(seg.get("tts_volume") or 1.0),
            "length_scale": float(seg.get("tts_length_scale") or 1.0),
        }
    duration_control_used = str(
        seg.get("duration_control_used")
        or meta19.get("duration_control_used")
        or "none"
    )
    meta22 = {
        **meta19,
        "expand_executed": bool(expand_executed and text_changed),
        "text_changed": bool(text_changed),
        "garbage_expand_blocked": garbage_blocked,
        "soft_pad_count": pad_n,
        "fill_ratio": fill,
        "underflow_ms": int(budget.underflow or 0),
        "overflow_ms": overflow_ms,
        "final_status": final_status,
        "tts_backend": str(seg.get("tts_backend") or ""),
        "tts_voice": str(seg.get("tts_voice") or ""),
        "tts_rate": ctrl["rate"],
        "tts_pitch": ctrl["pitch"],
        "tts_volume": ctrl["volume"],
        "tts_length_scale": ctrl["length_scale"],
        "duration_control_used": duration_control_used,
    }
    prev_22 = dict(seg.get("stage22") or {})
    seg["stage22"] = {**prev_22, **meta22}
    meta23 = {
        "duration_control_used": duration_control_used,
        "tts_length_scale": ctrl["length_scale"],
        "tts_rate": ctrl["rate"],
        "expand_executed": bool(expand_executed and text_changed),
        "text_changed": bool(text_changed),
        "fill_ratio": fill,
        "underflow_ms": int(budget.underflow or 0),
        "overflow_ms": overflow_ms,
        "final_status": final_status,
        "garbage_expand_blocked": garbage_blocked,
        "soft_pad_count": pad_n,
        "overlap_after_ripple": int(seg.get("overlap_after_ripple") or 0),
    }
    prev_23 = dict(seg.get("stage23") or {})
    seg["stage23"] = {**prev_23, **meta23}
    seg["unique_text_ok"] = unique_text_ok
    seg["clean_split_ok"] = clean_split_ok
    seg["fill_ratio"] = fill
    seg["text_changed"] = bool(text_changed)
    seg["garbage_expand_blocked"] = garbage_blocked
    seg["duration_control_used"] = duration_control_used


def _stamp_stage19b_meta(
    seg: dict,
    *,
    fit: Any,
    expand_required: bool,
    expand_executed: bool,
    algorithm_reason: str,
    text_changed: bool,
) -> None:
    fill = float(getattr(fit, "fill_ratio", 0.0) or 0.0)
    atempo = float(getattr(fit, "atempo", 1.0) or 1.0)
    strategy = str(getattr(fit, "strategy", "") or "ok")
    action = str(getattr(fit, "action", "") or "")
    expansion_strategy = "none"
    if expand_executed:
        reasons = [str(r) for r in (getattr(fit, "reasons", None) or [])]
        if "rule_expand" in reasons or "expand_to_fill" in " ".join(reasons):
            expansion_strategy = "rule_expand"
        else:
            expansion_strategy = "expand_to_fill"
    seg["expand_required"] = bool(expand_required)
    seg["expand_executed"] = bool(expand_executed)
    seg["expansion_strategy"] = expansion_strategy
    seg["fill_ratio"] = round(fill, 4)
    seg["atempo"] = round(atempo, 4)
    seg["strategy"] = strategy
    seg["rule_rewrite_used"] = bool(text_changed)
    lock_text_fit_algorithm_reason(seg, algorithm_reason)
    if text_changed or expand_executed:
        from engines.dub_engine_v2.adaptation_decision import mark_adaptation_executed

        stage = f"text_slot_fit:{action or strategy or 'fit'}"
        mark_adaptation_executed(seg, decision=algorithm_reason, stages=[stage])
        trace = seg.setdefault("text_adaptation_trace", {})
        trace["executed"] = True
        reasons = list(trace.get("reasons") or [])
        if algorithm_reason not in reasons:
            reasons.append(algorithm_reason)
        trace["reasons"] = reasons
        prev = list(trace.get("stages") or [])
        if stage not in prev:
            prev.append(stage)
        trace["stages"] = prev
    seg["stage19b"] = {
        "expand_required": bool(expand_required),
        "expand_executed": bool(expand_executed),
        "expansion_strategy": expansion_strategy,
        "algorithm_reason": algorithm_reason,
        "fill_ratio": round(fill, 4),
        "atempo": round(atempo, 4),
        "strategy": strategy,
        "rule_rewrite_used": bool(text_changed),
        "action": action,
        "reasons": list(getattr(fit, "reasons", None) or []),
    }


def _apply_stage23_duration_control(
    seg: dict,
    idx: int,
    timing_map: list,
    budget: TimingBudget,
    *,
    voice: str,
    work_dir: Path,
    regen_fn: Callable[..., Any] | None,
    commit_fn: Callable[..., Any] | None,
    tts_rate: str | None,
    tts_pitch: str | None,
    task_id: str | None,
    resolve_path: Callable[[str], str] | None,
) -> TimingBudget:
    """Stage 23: length_scale → rate → re-TTS (same text) before expand.

    Trigger: underflow_ms > 250 or fill_ratio < 0.92.
    """
    slot = int(budget.slot_duration or 0)
    meas = int(budget.measured_duration or 0)
    if slot <= 0 or meas <= 0:
        return budget
    fill = meas / float(slot)
    underflow = max(0, slot - meas)
    if underflow <= UNDERFLOW_TRIGGER_MS and fill >= STAGE23_OK_FILL_LO:
        return budget
    if regen_fn is None:
        return budget

    backend = str(seg.get("tts_backend") or seg.get("tts_engine") or "").lower()
    voice_l = str(seg.get("tts_voice") or voice or "").lower()
    is_mykyta = "tts_uk" in backend or "mykyta" in voice_l
    if not is_mykyta:
        try:
            from engines.tts_backends import get_pipeline_tts_backend

            pipe = str(get_pipeline_tts_backend() or "").lower()
            is_mykyta = pipe in ("tts_uk", "tts-uk")
        except Exception:
            is_mykyta = False
    if not is_mykyta:
        return budget

    try:
        from engines.tts_backends import (
            compute_mykyta_duration_controls,
            resolve_mykyta_controls,
            set_pipeline_mykyta_controls,
        )
    except Exception:
        return budget

    base = resolve_mykyta_controls(
        {
            "rate": seg.get("tts_rate", tts_rate),
            "pitch": seg.get("tts_pitch", tts_pitch),
            "volume": seg.get("tts_volume"),
            "length_scale": seg.get("tts_length_scale"),
        },
        env=False,
    )
    ctrl = compute_mykyta_duration_controls(slot, meas, base=base)
    # Need a meaningful stretch (length_scale up or rate down).
    if (
        ctrl["length_scale"] <= base["length_scale"] + 0.005
        and ctrl["rate"] >= base["rate"] - 0.005
    ):
        # Still apply when base is already near defaults but slot needs stretch.
        if ctrl["length_scale"] < 1.01 and fill >= 0.90:
            return budget

    text = _segment_text(seg)
    if not text:
        return budget

    used = "length_scale"
    if abs(ctrl["length_scale"] - base["length_scale"]) < 0.01 and abs(
        ctrl["rate"] - base["rate"]
    ) >= 0.01:
        used = "rate"

    try:
        set_pipeline_mykyta_controls(ctrl)
    except Exception:
        pass

    try:
        regen_result = regen_fn(
            text,
            voice=voice,
            tts_rate=str(ctrl["rate"]),
            tts_pitch=str(ctrl["pitch"]),
            length_scale=ctrl["length_scale"],
            volume=ctrl["volume"],
            mykyta_controls=ctrl,
            task_id=task_id,
            segment_index=idx,
            segment_id=str(seg.get("segment_id") or ""),
            engine_id=str(seg.get("tts_backend") or seg.get("tts_engine") or "tts_uk"),
        )
    except Exception as exc:
        logger.debug("stage23 duration_control regen skipped: %s", exc)
        return budget

    if isinstance(regen_result, tuple):
        new_file, new_ms = regen_result[0], int(regen_result[1] or 0)
    else:
        new_file, new_ms = regen_result, 0
    if not new_file:
        return budget

    seg["file"] = new_file
    seg["tts_file_path"] = new_file
    if new_ms <= 0:
        new_ms = measure_actual_ms(seg, resolve_path=resolve_path)
    else:
        seg["playback_duration"] = new_ms
        seg["tts_ms"] = new_ms
        seg["actual_duration_ms"] = new_ms
        seg["final_tts_duration_ms"] = new_ms

    seg["tts_rate"] = ctrl["rate"]
    seg["tts_pitch"] = ctrl["pitch"]
    seg["tts_volume"] = ctrl["volume"]
    seg["tts_length_scale"] = ctrl["length_scale"]
    seg["duration_control_used"] = used
    stages = list(seg.get("adaptation_stages") or [])
    tag = f"stage23:duration_control:{used}:ls={ctrl['length_scale']:.3f}:rate={ctrl['rate']:.3f}"
    if tag not in stages:
        stages.append(tag)
    seg["adaptation_stages"] = stages

    if commit_fn:
        try:
            commit_fn(
                None,
                [idx],
                tts_text=text,
                audio_filename=str(seg.get("file") or new_file),
            )
        except Exception as exc:
            logger.debug("stage23 duration_control commit skipped: %s", exc)

    saved_pause = budget.pause_adjustments_ms
    saved_stages = list(budget.pause_stages or [])
    orig = budget.original_duration
    iters = budget.rewrite_iterations
    reason = budget.rewrite_reason
    budget = build_timing_budget(seg, idx, timing_map)
    budget.rewrite_iterations = iters
    budget.rewrite_reason = reason or f"stage23:duration_control:{used}"
    budget.pause_adjustments_ms = saved_pause
    budget.pause_stages = saved_stages
    budget.original_duration = orig or int(
        seg.get("first_tts_duration_ms") or budget.measured_duration
    )
    slot_n = max(1, int(budget.slot_duration or 1))
    seg["fill_ratio"] = round(int(budget.measured_duration or 0) / float(slot_n), 4)
    return budget


def _apply_stage23_atempo_slow(
    seg: dict,
    *,
    budget: TimingBudget,
    work_dir: Path,
    resolve_path: Callable[[str], str] | None,
) -> TimingBudget:
    """Stage 23: if fill still < 0.88 after length_scale/expand → atempo slow [0.90, 1.0]."""
    slot = int(budget.slot_duration or 0)
    tts = int(budget.measured_duration or 0)
    if slot <= 0 or tts <= 0:
        return budget
    fill = tts / float(slot)
    if fill >= 0.88:
        return budget
    # Slow down to lengthen: atempo = measured/slot clamped to [0.90, 1.0].
    tempo = max(STAGE23_ATEMPO_SLOW_LO, min(STAGE23_ATEMPO_SLOW_HI, fill))
    if abs(tempo - 1.0) < 0.02:
        return budget

    src = str(seg.get("file") or seg.get("tts_file_path") or "").strip()
    if resolve_path and src:
        try:
            src = resolve_path(src) or src
        except Exception:
            pass
    if not src or not Path(src).is_file():
        return budget
    try:
        from engines.timing_fit import fit_segment_audio

        fitted, meta = fit_segment_audio(
            src,
            0,
            slot,
            work_dir=work_dir / "stage23_atempo_slow",
            allow_atempo=True,
            max_atempo=1.0,
            text_hint=_segment_text(seg),
        )
        if fitted and Path(fitted).is_file():
            seg["file"] = fitted
            seg["tts_file_path"] = fitted
            fitted_ms = int((meta or {}).get("fitted_ms") or (meta or {}).get("tts_ms") or 0)
            if fitted_ms <= 0:
                fitted_ms = measure_actual_ms(seg, resolve_path=resolve_path)
            if fitted_ms > 0:
                seg["playback_duration"] = fitted_ms
                seg["tts_ms"] = fitted_ms
                seg["actual_duration_ms"] = fitted_ms
                seg["final_tts_duration_ms"] = fitted_ms
            applied = float((meta or {}).get("atempo") or tempo)
            applied = max(STAGE23_ATEMPO_SLOW_LO, min(STAGE23_ATEMPO_SLOW_HI, applied))
            seg["atempo"] = round(applied, 4)
            seg["strategy"] = "atempo_slow"
            if str(seg.get("duration_control_used") or "none") in ("none", ""):
                seg["duration_control_used"] = "atempo"
            elif fill < STAGE23_OK_FILL_LO:
                seg["duration_control_used"] = "atempo"
            stages = list(seg.get("adaptation_stages") or [])
            tag = f"stage23_atempo_slow:{applied:.3f}"
            if tag not in stages:
                stages.append(tag)
            seg["adaptation_stages"] = stages
    except Exception as exc:
        logger.debug("stage23 atempo_slow skipped: %s", exc)
    return budget


def _apply_light_atempo_after_fit(
    seg: dict,
    *,
    budget: TimingBudget,
    work_dir: Path,
    resolve_path: Callable[[str], str] | None,
    fit: Any,
) -> TimingBudget:
    """Stage 19i: atempo only when |Δ|≤350 and ratio ∈ [0.90, 1.20]."""
    from engines.text_slot_fit import (
        ATEMPO_MAX,
        ATEMPO_MIN,
        UNDERFILL_EXPAND_RATIO,
        forbid_fast_then_gap,
        suggested_atempo_for_fill,
    )

    slot = int(budget.slot_duration or 0)
    tts = int(budget.measured_duration or 0)
    if slot <= 0 or tts <= 0:
        return budget
    fill = tts / float(slot)
    delta = abs(slot - tts)
    # Spec ratio = slot/measured; apply factor must stay in [ATEMPO_MIN, ATEMPO_MAX].
    ratio_slot_over_meas = slot / float(tts)
    tempo = float(getattr(fit, "atempo", 0) or 0) or suggested_atempo_for_fill(tts, slot)
    tempo = max(ATEMPO_MIN, min(ATEMPO_MAX, tempo))
    if forbid_fast_then_gap(tempo, fill):
        tempo = max(ATEMPO_MIN, min(1.0, fill))
    # Forbidden outside hard band or when |Δ| > 350.
    if delta > TEXT_FIT_DELTA_MS:
        return budget
    if not (ATEMPO_MIN <= ratio_slot_over_meas <= ATEMPO_MAX):
        return budget
    if not (ATEMPO_MIN <= tempo <= ATEMPO_MAX):
        return budget
    # Skip near-noop tempos.
    if abs(tempo - 1.0) < 0.02:
        return budget
    # Already inside comfortable fill band → skip.
    if fill >= UNDERFILL_EXPAND_RATIO and fill <= 1.08 and delta <= TEXT_FIT_DELTA_MS:
        return budget

    src = str(seg.get("file") or seg.get("tts_file_path") or "").strip()
    if resolve_path and src:
        try:
            src = resolve_path(src) or src
        except Exception:
            pass
    if not src or not Path(src).is_file():
        return budget
    try:
        from engines.timing_fit import fit_segment_audio

        fitted, meta = fit_segment_audio(
            src,
            0,
            slot,
            work_dir=work_dir / "stage19b_atempo",
            allow_atempo=True,
            max_atempo=ATEMPO_MAX,
            text_hint=_segment_text(seg),
        )
        if fitted and Path(fitted).is_file():
            seg["file"] = fitted
            seg["tts_file_path"] = fitted
            fitted_ms = int((meta or {}).get("fitted_ms") or (meta or {}).get("tts_ms") or 0)
            if fitted_ms <= 0:
                fitted_ms = measure_actual_ms(seg, resolve_path=resolve_path)
            if fitted_ms > 0:
                seg["playback_duration"] = fitted_ms
                seg["tts_ms"] = fitted_ms
                seg["actual_duration_ms"] = fitted_ms
            applied = float((meta or {}).get("atempo") or tempo)
            seg["atempo"] = round(applied, 4)
            if fill < UNDERFILL_EXPAND_RATIO:
                seg["strategy"] = (
                    "expand_then_slow"
                    if seg.get("expand_executed")
                    else "atempo_slow"
                )
            else:
                seg["strategy"] = (
                    "shorten_then_fast" if seg.get("rule_rewrite_used") else "ok"
                )
            stages = list(seg.get("adaptation_stages") or [])
            tag = f"stage19b_atempo:{applied:.3f}"
            if tag not in stages:
                stages.append(tag)
            seg["adaptation_stages"] = stages
            if not seg.get("adaptation_executed"):
                from engines.dub_engine_v2.adaptation_decision import (
                    mark_adaptation_executed,
                )

                mark_adaptation_executed(
                    seg,
                    decision="TextThenAtemo",
                    stages=[tag],
                )
            if not str(seg.get("algorithm_reason") or "").startswith("TextSlotFit"):
                seg["algorithm_reason"] = "TextThenAtemo"
                seg["text_adaptation_reason"] = "TextThenAtemo"
    except Exception as exc:
        logger.debug("stage19b light atempo skipped: %s", exc)
    return budget


def apply_stage19b_rule_text_fit(
    seg: dict,
    idx: int,
    timing_map: list,
    budget: TimingBudget,
    *,
    source_hint: str,
    target_lang: str,
    voice: str,
    work_dir: Path,
    regen_fn: Callable[..., Any] | None,
    commit_fn: Callable[..., Any] | None = None,
    audit: dict | None = None,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    task_id: str | None = None,
    resolve_path: Callable[[str], str] | None = None,
) -> tuple[TimingBudget, bool]:
    """Stage 19b/19c: expand/shorten text before tempo. Works without LLM.

    Stage 19c: text_changed ⇒ mandatory re-TTS (regen_fn required — fail loud).
    Returns (budget, did_attempt). did_attempt True when |delta|>350 and fit ran.
    """
    slot0 = max(1, int(budget.slot_duration or 0) or 1)
    meas0 = int(budget.measured_duration or 0)
    fill_now = (meas0 / float(slot0)) if meas0 > 0 else 0.0
    # Stage 23: enter on |Δ|>350, fill<0.92, or underflow>250.
    needs_fit = (
        _needs_stage19b_text_fit(budget)
        or (0 < fill_now < STAGE23_OK_FILL_LO)
        or int(budget.underflow or 0) > UNDERFLOW_TRIGGER_MS
        or _slot_delta_ms(budget) < -UNDERFLOW_TRIGGER_MS
    )
    if not needs_fit:
        return budget, False

    from engines.text_slot_fit import (
        MIN_CPS_UK,
        STAGE23_OK_FILL_LO,
        UNDERFILL_EXPAND_RATIO,
        char_budget,
        clean_text_chars,
        cps_over_budget,
        cps_under_budget,
        estimated_cps,
        expand_to_fill,
        fit_text_to_slot,
        is_clean_utterance,
        is_garbage_expand,
        safe_shorten,
        semantic_anchor_text,
        should_force_split,
        strip_garbage_expand_phrases,
        word_retention_ratio,
        MIN_WORD_RETENTION,
    )

    delta_before = _slot_delta_ms(budget)
    original = _segment_text(seg)
    if not original:
        return budget, False
    cps_now = estimated_cps(original, meas0) if meas0 > 0 else 0.0
    fill0 = float(meas0) / float(max(1, slot0)) if slot0 > 0 else 0.0
    # Stage 23: duration-control / expand on underflow >250 OR fill < 0.92.
    expand_required = (
        delta_before < -UNDERFLOW_TRIGGER_MS
        or fill0 < STAGE23_OK_FILL_LO
        or int(budget.underflow or 0) > UNDERFLOW_TRIGGER_MS
        or (0 < cps_now < MIN_CPS_UK)
        or cps_under_budget(original, slot0)
    )

    # Stage 23 primary lever: Mykyta length_scale/rate re-TTS before text expand.
    if expand_required and regen_fn is not None:
        budget = _apply_stage23_duration_control(
            seg,
            idx,
            timing_map,
            budget,
            voice=voice,
            work_dir=work_dir,
            regen_fn=regen_fn,
            commit_fn=commit_fn,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            task_id=task_id,
            resolve_path=resolve_path,
        )
        meas0 = int(budget.measured_duration or 0)
        slot0 = max(1, int(budget.slot_duration or 0) or 1)
        fill0 = float(meas0) / float(slot0) if meas0 > 0 else 0.0
        delta_before = _slot_delta_ms(budget)
        # Re-evaluate expand need after duration control.
        expand_required = (
            delta_before < -UNDERFLOW_TRIGGER_MS
            or fill0 < STAGE23_OK_FILL_LO
            or int(budget.underflow or 0) > UNDERFLOW_TRIGGER_MS
            or fill0 < 0.90
            or (0 < cps_now < MIN_CPS_UK)
            or cps_under_budget(original, slot0)
        )
    shorten_required = (
        delta_before > OVERFLOW_TRIGGER_MS
        or cps_over_budget(original, slot0)
    )

    raw_mt = semantic_anchor_text(seg, fallback=original)
    # Prefer longer semantic/raw as expand source (Stage 19d §B).
    for key in ("semantic_engine_text", "raw_translation", "raw_mt"):
        cand = " ".join(str(seg.get(key) or "").split()).strip()
        if cand and len(cand.split()) > len(raw_mt.split()):
            raw_mt = cand
    expansion_strategy = "none"
    llm_used = False
    shorten_executed = bool(seg.get("shorten_executed"))

    # Stage 19e §B: predicted >> slot → forced split (not safe_shorten into one slot).
    slot_for_split = int(budget.slot_duration or 0)
    if (
        (shorten_required or seg.get("needs_post_restore_split") or seg.get("truncation_blocked"))
        and should_force_split(original, slot_for_split, str(target_lang or "uk"))
        and not seg.get("stage19e_split_done")
        and not seg.get("stage19c_split_done")
    ):
        seg["needs_post_restore_split"] = True
        lock_text_fit_algorithm_reason(seg, "TextSlotFitSplit")
        budget.final_status = "stage19e_partial"
        budget.rewrite_reason = "stage19e:force_split_pending"
        _stamp_stage19e_fields(
            seg,
            budget=budget,
            algorithm_reason="TextSlotFitSplit",
            expand_executed=bool(seg.get("expand_executed")),
            shorten_executed=shorten_executed,
            split_executed=False,
        )
        return budget, True

    fit = fit_text_to_slot(
        original,
        int(budget.slot_duration or 0),
        str(target_lang or "uk"),
        source_hint=str(source_hint or ""),
        allow_expand=True,
        raw_mt=raw_mt,
    )
    new_text = " ".join(str(fit.text or "").split()).strip()
    text_changed = bool(fit.changed and new_text and new_text != original)

    # Stage 19g §B: forced expand — text MUST grow (up to 2 attempts).
    if expand_required and not text_changed:
        seg["requires_strong_expand"] = True
        slot = int(budget.slot_duration or 0)
        preferred = raw_mt if len(raw_mt.split()) > len(original.split()) else original
        prefer_anchor = semantic_anchor_text(seg, fallback=preferred)
        if len(prefer_anchor.split()) < len(preferred.split()):
            prefer_anchor = preferred
        base_for_expand = original
        for _attempt in range(2):
            try:
                exp_text, exp_reasons = expand_to_fill(
                    base_for_expand,
                    target_ms=int(slot),  # Stage 23: fill vs full slot (0.92–1.12)
                    lang=str(target_lang or "uk"),
                    source_hint=str(source_hint or ""),
                    raw_mt=preferred,
                    prefer_raw=prefer_anchor,
                    target_chars=char_budget(slot),
                    strategy_order=(
                        "semantic_repeat_key",
                        "glossary_full_term",
                        "soft_pad_whitelist_once",
                        "soft_pad_whitelist_twice",
                    ),
                )
                exp_text = strip_garbage_expand_phrases(
                    " ".join(str(exp_text or "").split()).strip()
                )
                blocked_n = 0
                for r in exp_reasons or []:
                    rs = str(r)
                    if "garbage_expand_blocked" in rs or "expand_refused" in rs:
                        blocked_n += 1
                    elif "blocked_count:" in rs:
                        try:
                            blocked_n += int(rs.rsplit(":", 1)[-1])
                        except ValueError:
                            blocked_n += 1
                if blocked_n:
                    seg["garbage_expand_blocked"] = int(
                        seg.get("garbage_expand_blocked") or 0
                    ) + blocked_n
                grew = bool(
                    exp_text
                    and len(clean_text_chars(exp_text))
                    > len(clean_text_chars(original))
                    and exp_text != original
                    and not is_garbage_expand(exp_text)
                    and (
                        is_clean_utterance(exp_text)
                        or len(exp_text.split()) >= len(original.split())
                    )
                )
                if grew:
                    new_text = exp_text
                    text_changed = True
                    fit.changed = True
                    fit.text = new_text
                    fit.action = "expand"
                    fit.strategy = "expand"
                    fit.reasons = list(getattr(fit, "reasons", None) or []) + list(
                        exp_reasons or []
                    ) + ["stage19j:forced_expand"]
                    if any(
                        x in (exp_reasons or [])
                        for x in (
                            "raw_prefer",
                            "stage19f:force_raw_prefer",
                            "stage19g:force_raw_prefer",
                            "stage19i:force_raw_prefer",
                            "stage19j:force_raw_prefer",
                        )
                    ):
                        expansion_strategy = "raw_prefer"
                    elif "rule_expand" in (exp_reasons or []):
                        expansion_strategy = (
                            "rule_expand_from_raw"
                            if preferred != original
                            else "rule_expand"
                        )
                    elif any(
                        str(r).startswith("stage19j:")
                        or str(r).startswith("stage19i:")
                        or str(r).startswith("stage19g:")
                        for r in (exp_reasons or [])
                    ):
                        expansion_strategy = "expand_to_fill"
                    else:
                        expansion_strategy = "expand_to_fill"
                    break
                base_for_expand = exp_text or base_for_expand
            except Exception as exc:
                logger.debug("stage19j forced expand_to_fill skipped: %s", exc)
        # Nuclear: longer semantic/raw only if clean (never garbage crumbs).
        if (
            not text_changed
            and prefer_anchor
            and prefer_anchor != original
            and len(clean_text_chars(prefer_anchor)) > len(clean_text_chars(original))
            and not has_forbidden_expand_pattern(prefer_anchor)
            and is_clean_utterance(prefer_anchor)
        ):
            new_text = prefer_anchor
            text_changed = True
            expansion_strategy = "raw_prefer"
            fit.changed = True
            fit.text = new_text
            fit.action = "expand"
            fit.strategy = "expand"
            fit.reasons = list(getattr(fit, "reasons", None) or []) + [
                "stage19j:force_raw_prefer"
            ]
        if not text_changed:
            try:
                from engines.translation_adapt import llm_rephrase_available
                from engines.semantic_optimizer import optimize_expand_for_slot

                if llm_rephrase_available():
                    opt = optimize_expand_for_slot(
                        original,
                        source_hint=source_hint,
                        slot_ms=budget.slot_duration,
                        tgt_lang=target_lang,
                        max_rounds=1,
                        current_ms=int(budget.measured_duration or 0),
                    )
                    if opt.changed and str(opt.text or "").strip() != original:
                        new_text = " ".join(str(opt.text).split()).strip()
                        text_changed = True
                        llm_used = True
                        expansion_strategy = "llm_expand"
                        fit.changed = True
                        fit.text = new_text
                        fit.action = "expand"
                        fit.strategy = "expand"
            except Exception as exc:
                logger.debug("stage19g strong LLM expand skipped: %s", exc)

    # Stage 19d §C: forced shorten when overflow (split handled upstream for large ov).
    if (
        shorten_required
        and not text_changed
        and not large_overflow_needs_split(
            overflow_ms=max(0, delta_before), slot_ms=int(budget.slot_duration or 0)
        )
    ):
        try:
            shortened, sh_reasons, _tr = safe_shorten(
                original,
                int(budget.slot_duration or 0),
                str(target_lang or "uk"),
                source_hint=str(source_hint or ""),
            )
            shortened = " ".join(str(shortened or "").split()).strip()
            if (
                shortened
                and shortened != original
                and word_retention_ratio(original, shortened) >= MIN_WORD_RETENTION
            ):
                new_text = shortened
                text_changed = True
                shorten_executed = True
                fit.changed = True
                fit.text = new_text
                fit.action = "shorten"
                fit.strategy = "shorten"
                fit.reasons = list(getattr(fit, "reasons", None) or []) + list(
                    sh_reasons or []
                )
        except Exception as exc:
            logger.debug("stage19d forced safe_shorten skipped: %s", exc)
        if not text_changed:
            try:
                from engines.translation_adapt import llm_rephrase_available
                from engines.semantic_optimizer import optimize_llm_rephrase_for_slot

                if llm_rephrase_available():
                    opt = optimize_llm_rephrase_for_slot(
                        original,
                        source_hint=source_hint,
                        slot_ms=budget.slot_duration,
                        tgt_lang=target_lang,
                        max_rounds=1,
                        current_ms=int(budget.measured_duration or 0),
                    )
                    if opt.changed and str(opt.text or "").strip() != original:
                        new_text = " ".join(str(opt.text).split()).strip()
                        text_changed = True
                        llm_used = True
                        shorten_executed = True
                        fit.changed = True
                        fit.text = new_text
                        fit.action = "shorten"
                        fit.strategy = "shorten"
            except Exception as exc:
                logger.debug("stage19d LLM shorten skipped: %s", exc)

    expand_executed = bool(
        expand_required
        and text_changed
        and (
            fit.action in ("expand", "expand_then_slow")
            or "expand" in " ".join(str(r) for r in (fit.reasons or []))
            or len(new_text.split()) > len(original.split())
            or expansion_strategy in ("llm_expand", "raw_prefer", "rule_expand", "rule_expand_from_raw", "expand_to_fill")
        )
    )
    if expand_executed:
        seg["duration_control_used"] = "expand"
    if text_changed and fit.action == "shorten":
        shorten_executed = True
    if text_changed and expansion_strategy == "none":
        if expand_executed:
            reasons = [str(r) for r in (fit.reasons or [])]
            expansion_strategy = (
                "llm_expand"
                if llm_used or "llm_expand" in reasons
                else (
                    "raw_prefer"
                    if "raw_prefer" in reasons
                    else ("rule_expand" if "rule_expand" in reasons else "expand_to_fill")
                )
            )

    algorithm_reason = _stage19b_algorithm_reason(fit, text_changed=text_changed)
    # Stage 19f: never advertise TextSlotFitExpand before expand actually ran.
    if expand_required and not expand_executed:
        algorithm_reason = "dead_air_risk"
    algorithm_reason = _stage19d_sanitize_algorithm_reason(
        algorithm_reason,
        expand_executed=expand_executed,
        shorten_executed=shorten_executed,
        split_executed=bool(seg.get("split_executed") or seg.get("stage19c_split_done")),
        delta_ms=delta_before,
        underflow_ms=int(budget.underflow or 0),
        overflow_ms=int(budget.overflow or 0),
    )
    regen_ok = False

    if text_changed:
        if regen_fn is None:
            seg["expand_required"] = expand_required
            lock_text_fit_algorithm_reason(seg, algorithm_reason)
            budget.final_status = "failed_no_regen"
            budget.rewrite_reason = "stage19d:PIPELINE_TEXT_FIT_NO_REGEN"
            raise TextFitNoRegenError(
                "Stage19d requires regen_fn (PIPELINE_TEXT_FIT_NO_REGEN)"
            )

        regen_result = regen_fn(
            new_text,
            voice=voice,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            task_id=task_id,
            segment_index=idx,
            segment_id=str(seg.get("segment_id") or ""),
        )
        if isinstance(regen_result, tuple):
            new_file, new_ms = regen_result[0], int(regen_result[1] or 0)
        else:
            new_file, new_ms = regen_result, 0
        if not new_file:
            budget.final_status = "failed_tts_regen"
            budget.rewrite_reason = f"stage19c:tts_fail:{algorithm_reason}"
            seg["expand_required"] = expand_required
            lock_text_fit_algorithm_reason(seg, algorithm_reason)
            seg["stage19c"] = {
                "delta_before": delta_before,
                "delta_after": delta_before,
                "split_children": 0,
                "regen_ok": False,
                "error": "failed_tts_regen",
            }
            # Do not continue as success with the old wav.
            return budget, True

        regen_ok = True
        seg["text"] = new_text
        seg["plain_text"] = new_text
        seg["translation_text"] = new_text
        seg["final_text"] = new_text
        seg["final_tts_text"] = new_text
        seg["file"] = new_file
        seg["tts_file_path"] = new_file
        if new_ms <= 0:
            new_ms = measure_actual_ms(seg, resolve_path=resolve_path)
        else:
            seg["playback_duration"] = new_ms
            seg["tts_ms"] = new_ms
            seg["actual_duration_ms"] = new_ms
            seg["final_tts_duration_ms"] = new_ms

        pause_meta = apply_dynamic_pause_engine(
            seg,
            slot_ms=budget.slot_duration,
            work_dir=work_dir / "pause",
            resolve_path=resolve_path,
        )
        if pause_meta.get("applied"):
            budget.pause_adjustments_ms += int(pause_meta.get("pause_adjustments_ms") or 0)
            budget.pause_stages.extend(list(pause_meta.get("stages") or []))

        if audit is not None:
            audit["tts_text"] = new_text
            audit["final_text"] = new_text
            qd = audit.setdefault("quality_details", {})
            qd["stage19c"] = {
                "reason": algorithm_reason,
                "text_before": original[:500],
                "text_after": new_text[:500],
                "expand_required": expand_required,
                "expand_executed": expand_executed,
                "fill_ratio": fit.fill_ratio,
                "atempo": fit.atempo,
                "strategy": fit.strategy,
                "regen_ok": True,
            }

        if commit_fn:
            try:
                commit_fn(
                    None,
                    [idx],
                    tts_text=new_text,
                    audio_filename=str(seg.get("file") or new_file),
                )
            except Exception as exc:
                logger.debug("stage19c commit skipped: %s", exc)

        budget.rewrite_iterations = max(1, int(budget.rewrite_iterations or 0) + 1)
        seg["rewrite_iterations"] = int(budget.rewrite_iterations)
        budget.rewrite_reason = (
            f"stage19g:{algorithm_reason}" if expand_executed else f"stage19d:{algorithm_reason}"
        )
        seg["text_changed"] = True
        # Expanded text may now require post-restore split (raw >> slot).
        if expand_executed and should_force_split(
            new_text, int(budget.slot_duration or 0), str(target_lang or "uk")
        ):
            seg["needs_post_restore_split"] = True
        saved_pause = budget.pause_adjustments_ms
        saved_stages = list(budget.pause_stages or [])
        orig = budget.original_duration
        iters = budget.rewrite_iterations
        reason = budget.rewrite_reason
        budget = build_timing_budget(seg, idx, timing_map)
        budget.rewrite_iterations = iters
        budget.rewrite_reason = reason
        budget.pause_adjustments_ms = saved_pause
        budget.pause_stages = saved_stages
        budget.original_duration = orig or int(
            seg.get("first_tts_duration_ms") or budget.measured_duration
        )
    else:
        if expand_required:
            seg["expand_required"] = True
            seg["requires_strong_expand"] = True
            seg["expand_executed"] = False
            # Stage 23: dead_air only when fill still < 0.92 after expand attempt.
            slot_chk = max(1, int(budget.slot_duration or 1))
            fill_chk = float(int(budget.measured_duration or 0)) / float(slot_chk)
            if fill_chk < STAGE23_OK_FILL_LO:
                budget.final_status = "dead_air_risk"
                seg["strategy"] = "dead_air_risk"
                budget.rewrite_reason = "stage23:expand_no_change"
                algorithm_reason = "dead_air_risk"
                lock_text_fit_algorithm_reason(seg, "dead_air_risk")
            else:
                budget.rewrite_reason = "stage23:expand_skipped_fill_ok"
            seg["text_changed"] = False

    seg["shorten_executed"] = bool(shorten_executed)
    if expand_executed:
        seg["expand_executed"] = True
    elif expand_required and not text_changed:
        seg["expand_executed"] = False
    _stamp_stage19b_meta(
        seg,
        fit=fit,
        expand_required=expand_required,
        expand_executed=expand_executed,
        algorithm_reason=algorithm_reason,
        text_changed=text_changed,
    )
    if expansion_strategy != "none":
        seg["expansion_strategy"] = expansion_strategy
    lock_text_fit_algorithm_reason(seg, algorithm_reason)

    # Light atempo AFTER text fit / re-TTS only (never sole strategy when |Δ|>350).
    if budget.final_status != "failed_tts_regen" and (
        _needs_stage19b_text_fit(budget)
        or float(seg.get("fill_ratio") or 0) < STAGE23_OK_FILL_LO
    ):
        budget = _apply_light_atempo_after_fit(
            seg,
            budget=budget,
            work_dir=work_dir,
            resolve_path=resolve_path,
            fit=fit,
        )
        # Stage 23: if still fill < 0.88 → dedicated atempo_slow [0.90, 1.0].
        slot_chk = max(1, int(budget.slot_duration or 1))
        fill_chk = float(int(budget.measured_duration or 0)) / float(slot_chk)
        if fill_chk < 0.88:
            budget = _apply_stage23_atempo_slow(
                seg,
                budget=budget,
                work_dir=work_dir,
                resolve_path=resolve_path,
            )
        saved_pause = budget.pause_adjustments_ms
        saved_stages = list(budget.pause_stages or [])
        orig = budget.original_duration
        iters = budget.rewrite_iterations
        reason = budget.rewrite_reason
        budget = build_timing_budget(seg, idx, timing_map)
        budget.rewrite_iterations = iters
        budget.rewrite_reason = reason or f"stage19d:{algorithm_reason}"
        budget.pause_adjustments_ms = saved_pause
        budget.pause_stages = saved_stages
        budget.original_duration = orig or int(
            seg.get("first_tts_duration_ms") or budget.measured_duration
        )
        slot = max(1, int(budget.slot_duration or 1))
        seg["fill_ratio"] = round(int(budget.measured_duration or 0) / float(slot), 4)
        if text_changed and abs(_slot_delta_ms(budget)) > TEXT_FIT_DELTA_MS:
            # TextThenAtemo only when expand/shorten already ran.
            if expand_executed or shorten_executed:
                algorithm_reason = "TextThenAtemo"
            algorithm_reason = _stage19d_sanitize_algorithm_reason(
                algorithm_reason,
                expand_executed=expand_executed,
                shorten_executed=shorten_executed,
                split_executed=bool(seg.get("split_executed")),
                delta_ms=_slot_delta_ms(budget),
                underflow_ms=int(budget.underflow or 0),
                overflow_ms=int(budget.overflow or 0),
            )
            lock_text_fit_algorithm_reason(seg, algorithm_reason)

    delta_after = _slot_delta_ms(budget)
    prev19c = dict(seg.get("stage19c") or {})
    seg["stage19c"] = {
        **prev19c,
        "delta_before": int(delta_before),
        "delta_after": int(delta_after),
        "split_children": int(prev19c.get("split_children") or 0),
        "regen_ok": bool(regen_ok) if text_changed else prev19c.get("regen_ok"),
        "expansion_strategy": seg.get("expansion_strategy") or expansion_strategy,
        "algorithm_reason": str(seg.get("algorithm_reason") or algorithm_reason),
        "fill_ratio": seg.get("fill_ratio"),
        "atempo": seg.get("atempo"),
        "requires_strong_expand": bool(seg.get("requires_strong_expand")),
    }

    needs, _ = _needs_rewrite(budget)
    fill = float(seg.get("fill_ratio") or 0)
    if not needs and abs(delta_after) <= TEXT_FIT_DELTA_MS:
        budget.final_status = "ok"
    elif abs(delta_after) > STAGE19D_HARD_DELTA_MS:
        if int(budget.underflow or 0) > STAGE19D_HARD_DELTA_MS:
            budget.final_status = "dead_air_risk"
            seg["strategy"] = "dead_air_risk"
        elif int(budget.overflow or 0) > STAGE19D_HARD_DELTA_MS:
            budget.final_status = "overflow_unresolved"
        else:
            budget.final_status = "stage19d_partial"
    elif fill < UNDERFILL_EXPAND_RATIO and int(budget.underflow or 0) > TEXT_FIT_DELTA_MS:
        budget.final_status = (
            "stage19d_partial" if expand_executed else "dead_air_risk"
        )
        if budget.final_status == "dead_air_risk":
            seg["strategy"] = "dead_air_risk"
    elif budget.final_status not in ("failed_tts_regen", "failed_no_regen"):
        budget.final_status = "ok" if not needs else "stage19d_partial"

    _stamp_stage19d_fields(
        seg,
        budget=budget,
        algorithm_reason=str(seg.get("algorithm_reason") or algorithm_reason),
        expand_executed=expand_executed,
        shorten_executed=shorten_executed,
    )
    if not str(budget.rewrite_reason or "").startswith("stage19d"):
        budget.rewrite_reason = f"stage19d:{seg.get('algorithm_reason') or algorithm_reason}"

    logger.info(
        "[Stage19d] task=%s seg=%d δ %d→%d expand_req=%s expand_exec=%s "
        "shorten_exec=%s reason=%s fill=%.2f atempo=%.3f iters=%d regen_ok=%s",
        task_id,
        idx,
        delta_before,
        delta_after,
        expand_required,
        expand_executed,
        shorten_executed,
        seg.get("algorithm_reason") or algorithm_reason,
        float(seg.get("fill_ratio") or 0),
        float(seg.get("atempo") or 1),
        int(budget.rewrite_iterations or 0),
        regen_ok,
    )
    return budget, True


def _finalize_closed_loop_segment(
    seg: dict,
    idx: int,
    timing_map: list,
    budget: TimingBudget,
    *,
    source_hint: str,
    target_lang: str,
    voice: str,
    regen_fn: Callable[..., Any] | None,
    commit_fn: Callable[..., Any] | None = None,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    task_id: str | None = None,
    resolve_path: Callable[[str], str] | None = None,
) -> TimingBudget:
    """Stage 19d exit: anti-truncate guard + honest metadata before return."""
    if seg.get("stage19c_split_done"):
        seg["split_executed"] = True
    try:
        assert_no_silent_truncate(
            seg,
            slot_ms=int(budget.slot_duration or seg.get("slot_ms") or 0),
            lang=str(target_lang or "uk"),
            source_hint=str(source_hint or ""),
        )
    except NeedReTTS as need:
        if regen_fn is None:
            raise TextFitNoRegenError(
                "Stage19d requires regen_fn (anti_truncate)"
            ) from need
        new_text = _segment_text(seg)
        rr = regen_fn(
            new_text,
            voice=voice,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            task_id=task_id,
            segment_index=idx,
            segment_id=str(seg.get("segment_id") or ""),
        )
        if isinstance(rr, tuple):
            new_file, new_ms = rr[0], int(rr[1] or 0)
        else:
            new_file, new_ms = rr, 0
        if not new_file:
            budget.final_status = "failed_tts_regen"
            budget.rewrite_reason = "stage19d:anti_truncate_tts_fail"
        else:
            seg["file"] = new_file
            seg["tts_file_path"] = new_file
            seg["final_tts_text"] = new_text
            if new_ms <= 0:
                new_ms = measure_actual_ms(seg, resolve_path=resolve_path)
            else:
                seg["playback_duration"] = new_ms
                seg["tts_ms"] = new_ms
                seg["actual_duration_ms"] = new_ms
            if commit_fn:
                try:
                    commit_fn(
                        None,
                        [idx],
                        tts_text=new_text,
                        audio_filename=str(new_file),
                    )
                except Exception:
                    pass
            iters = max(1, int(budget.rewrite_iterations or 0) + 1)
            reason = "stage19d:anti_truncate_restored"
            pause = budget.pause_adjustments_ms
            stages = list(budget.pause_stages or [])
            orig = budget.original_duration
            budget = build_timing_budget(seg, idx, timing_map)
            budget.rewrite_iterations = iters
            budget.rewrite_reason = reason
            budget.pause_adjustments_ms = pause
            budget.pause_stages = stages
            budget.original_duration = orig
            budget.final_status = (
                "ok"
                if abs(_slot_delta_ms(budget)) <= TEXT_FIT_DELTA_MS
                else "stage19d_partial"
            )
            # Stage 19e: restored giant text into a tiny slot → force split upstream.
            if segment_needs_stage19e_split(
                seg,
                slot_ms=int(budget.slot_duration or 0),
                measured_ms=int(budget.measured_duration or 0),
                lang=str(target_lang or "uk"),
            ):
                seg["needs_post_restore_split"] = True
                budget.final_status = "stage19e_partial"
                budget.rewrite_reason = "stage19e:needs_post_restore_split"
                lock_text_fit_algorithm_reason(seg, "TextSlotFitSplit")

    # Also gate when Final already predicts >> slot (even without anti-truncate).
    if segment_needs_stage19e_split(
        seg,
        slot_ms=int(budget.slot_duration or seg.get("slot_ms") or 0),
        measured_ms=int(budget.measured_duration or 0),
        lang=str(target_lang or "uk"),
    ):
        seg["needs_post_restore_split"] = True
        if budget.final_status in ("ok", "pending", "stage19d_partial"):
            budget.final_status = "stage19e_partial"
        if not str(budget.rewrite_reason or "").startswith("stage19e"):
            budget.rewrite_reason = "stage19e:needs_post_restore_split"

    _stamp_stage19d_fields(
        seg,
        budget=budget,
        algorithm_reason=str(seg.get("algorithm_reason") or ""),
        expand_executed=bool(seg.get("expand_executed")),
        shorten_executed=bool(seg.get("shorten_executed")),
        split_executed=bool(seg.get("split_executed")),
    )
    # Honest final_status: no silent TIMING OK with dead air / truncate flags.
    if seg.get("truncation_blocked") and budget.final_status == "ok":
        if abs(_slot_delta_ms(budget)) > TEXT_FIT_DELTA_MS:
            budget.final_status = "stage19d_partial"
    if budget.final_status == "ok" and abs(_slot_delta_ms(budget)) > STAGE19D_HARD_DELTA_MS:
        budget.final_status = (
            "dead_air_risk"
            if int(budget.underflow or 0) > STAGE19D_HARD_DELTA_MS
            else "overflow_unresolved"
        )
    # Stage 19e: underfill without real expand is never ok.
    if (
        int(budget.underflow or 0) > TEXT_FIT_DELTA_MS
        and not seg.get("expand_executed")
        and not seg.get("split_executed")
        and not seg.get("needs_post_restore_split")
        and budget.final_status == "ok"
    ):
        budget.final_status = "dead_air_risk"

    seg["timing_budget"] = budget.to_dict()
    seg["timing_score"] = budget.timing_score
    seg["closed_loop"] = {
        "iterations": budget.rewrite_iterations,
        "final_status": budget.final_status,
        "rewrite_reason": budget.rewrite_reason,
        "pause_adjustments_ms": budget.pause_adjustments_ms,
        "timing_score": budget.timing_score,
        "actual_duration_ms": budget.measured_duration,
        "slot_duration": budget.slot_duration,
        "delta": budget.delta,
        "underflow_ms": max(0, int(budget.underflow or 0)),
        "expand_required": bool(seg.get("expand_required")),
        "expand_executed": bool(seg.get("expand_executed")),
        "shorten_executed": bool(seg.get("shorten_executed")),
        "split_executed": bool(seg.get("split_executed")),
        "post_restore_split": bool(seg.get("post_restore_split")),
        "truncation_blocked": bool(seg.get("truncation_blocked")),
        "retention_score": seg.get("retention_score"),
        "fill_ratio": seg.get("fill_ratio"),
        "algorithm_reason": seg.get("algorithm_reason") or "",
        "adaptation_skip_reason": seg.get("adaptation_skip_reason") or "",
        "adaptation_decision": seg.get("adaptation_decision") or {},
        "stage19e": seg.get("stage19e") or {},
    }
    return budget


def run_closed_loop_segment(
    seg: dict,
    idx: int,
    timing_map: list,
    *,
    source_hint: str,
    target_lang: str,
    src_lang: str,
    voice: str,
    work_dir: Path,
    regen_fn: Callable[..., Any] | None,
    commit_fn: Callable[..., Any] | None = None,
    audit: dict | None = None,
    max_iterations: int = MAX_REWRITE_ITERATIONS,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    task_id: str | None = None,
    resolve_path: Callable[[str], str] | None = None,
) -> TimingBudget:
    """Closed loop for one segment — never shifts neighbors."""
    from engines.semantic_optimizer import (
        optimize_expand_for_slot,
        optimize_llm_rephrase_for_slot,
    )

    def _done(b: TimingBudget) -> TimingBudget:
        return _finalize_closed_loop_segment(
            seg,
            idx,
            timing_map,
            b,
            source_hint=source_hint,
            target_lang=target_lang,
            voice=voice,
            regen_fn=regen_fn,
            commit_fn=commit_fn,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            task_id=task_id,
            resolve_path=resolve_path,
        )

    if not seg.get("first_tts_duration_ms"):
        first = measure_actual_ms(seg, resolve_path=resolve_path)
        if first > 0:
            seg["first_tts_duration_ms"] = first

    try:
        from engines.dub_engine_v2.adaptation_decision import stamp_need_adaptation_gate

        stamp_need_adaptation_gate(seg, index=idx, source="closed_loop_entry")
    except Exception:
        pass

    budget = build_timing_budget(seg, idx, timing_map)
    budget.original_duration = int(seg.get("first_tts_duration_ms") or budget.measured_duration)

    # 1) Dynamic Pause Engine before any rewrite
    pause_meta = apply_dynamic_pause_engine(
        seg,
        slot_ms=budget.slot_duration,
        work_dir=work_dir / "pause",
        resolve_path=resolve_path,
    )
    if pause_meta.get("applied"):
        budget.pause_adjustments_ms = int(pause_meta.get("pause_adjustments_ms") or 0)
        budget.pause_stages = list(pause_meta.get("stages") or [])
        budget = build_timing_budget(seg, idx, timing_map)
        budget.pause_adjustments_ms = int(pause_meta.get("pause_adjustments_ms") or 0)
        budget.pause_stages = list(pause_meta.get("stages") or [])
        budget.original_duration = int(
            seg.get("first_tts_duration_ms") or budget.measured_duration
        )

    needs, reason = _needs_rewrite(budget)
    # Stage 19b: |delta|>350 always needs text fit — never FitsNoChange / audio-only.
    if not needs and _needs_stage19b_text_fit(budget):
        needs = True
        reason = (
            "duration_overflow"
            if _slot_delta_ms(budget) > 0
            else "duration_underflow"
        )

    if not needs:
        budget.final_status = "ok"
        budget.rewrite_reason = "fits_after_pause" if pause_meta.get("applied") else "fits_no_change"
        if pause_meta.get("applied") and not seg.get("adaptation_executed"):
            from engines.dub_engine_v2.adaptation_decision import mark_adaptation_executed

            mark_adaptation_executed(seg, decision="pause_optimization", stages=["pause"])
        elif not seg.get("adaptation_executed"):
            from engines.dub_engine_v2.adaptation_decision import (
                SKIP_FITS_NO_CHANGE,
                mark_adaptation_skipped,
                resolve_need_adaptation,
            )

            _need = resolve_need_adaptation(
                seg,
                need_adaptation=False,
                overflow_ms=int(budget.overflow or 0),
                underflow_ms=int(budget.underflow or 0),
            )
            # FitsNoChange only when truly in band (|delta|≤350). Never with need=True.
            if _need or _needs_stage19b_text_fit(budget):
                try:
                    budget, _ = apply_stage19b_rule_text_fit(
                        seg,
                        idx,
                        timing_map,
                        budget,
                        source_hint=source_hint,
                        target_lang=target_lang,
                        voice=voice,
                        work_dir=work_dir,
                        regen_fn=regen_fn,
                        commit_fn=commit_fn,
                        audit=audit,
                        tts_rate=tts_rate,
                        tts_pitch=tts_pitch,
                        task_id=task_id,
                        resolve_path=resolve_path,
                    )
                except TextFitNoRegenError as exc:
                    budget.final_status = "failed_no_regen"
                    budget.rewrite_reason = str(exc.error_code)
                    logger.error("closed_loop seg=%s: %s", idx, exc)
                return _done(budget)
            mark_adaptation_skipped(
                seg,
                skip_reason=SKIP_FITS_NO_CHANGE,
                index=idx,
                overflow_ms=int(budget.overflow or 0),
                underflow_ms=int(budget.underflow or 0),
                need_adaptation=False,
                decision="fits_no_change",
            )
        return _done(budget)

    # Stage 19b/19c/19d: rule expand/shorten BEFORE LLM / before pause-only short-circuit.
    # Happy Path (max_iterations=0) and llm_available=false must still run text fit.
    stage19b_ran = False
    try:
        budget, stage19b_ran = apply_stage19b_rule_text_fit(
            seg,
            idx,
            timing_map,
            budget,
            source_hint=source_hint,
            target_lang=target_lang,
            voice=voice,
            work_dir=work_dir,
            regen_fn=regen_fn,
            commit_fn=commit_fn,
            audit=audit,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            task_id=task_id,
            resolve_path=resolve_path,
        )
    except TextFitNoRegenError as exc:
        budget.final_status = "failed_no_regen"
        budget.rewrite_reason = str(exc.error_code)
        stage19b_ran = True
        logger.error("closed_loop seg=%s: %s", idx, exc)
        return _done(budget)
    needs, reason = _needs_rewrite(budget)
    if not needs and abs(_slot_delta_ms(budget)) <= TEXT_FIT_DELTA_MS:
        budget.final_status = "ok"
        if not budget.rewrite_reason:
            budget.rewrite_reason = (
                "stage19d_fit" if stage19b_ran else "fits_after_pause"
            )
        return _done(budget)

    # max_iterations<=0 → no LLM loops (TZ §11 / Happy Path), but Stage 19d text-fit already ran.
    if int(max_iterations or 0) <= 0:
        if stage19b_ran and int(budget.rewrite_iterations or 0) > 0:
            budget.final_status = (
                "ok"
                if not needs and abs(_slot_delta_ms(budget)) <= TEXT_FIT_DELTA_MS
                else (budget.final_status or "stage19d_partial")
            )
            if not budget.rewrite_reason:
                budget.rewrite_reason = "stage19d_rule_fit"
        else:
            budget.final_status = "ok" if not needs else (
                budget.final_status or "stage19d_partial"
            )
            if not budget.rewrite_reason:
                budget.rewrite_reason = (
                    "stage19d_atempo_or_pause"
                    if stage19b_ran
                    else "stage19d_unresolved"
                )
        return _done(budget)

    if regen_fn is None:
        budget.final_status = "failed_no_regen"
        budget.rewrite_reason = reason
        seg["requires_llm_adaptation"] = True
        if not seg.get("adaptation_executed"):
            from engines.dub_engine_v2.adaptation_decision import (
                SKIP_NO_REGEN_CALLBACK,
                mark_adaptation_skipped,
            )

            mark_adaptation_skipped(
                seg,
                skip_reason=SKIP_NO_REGEN_CALLBACK,
                index=idx,
                overflow_ms=int(budget.overflow or 0),
                underflow_ms=int(budget.underflow or 0),
                need_adaptation=True,
                decision="skip_no_regen",
            )
        return _done(budget)

    # Freeze TZ P0: after TRANSLATION LOCK text rewrite is forbidden.
    # Overflow becomes a normal state for Studio (manual edit), not silent text fix.
    try:
        from engines.pipeline_integrity.translation_lock import is_segment_locked
    except ImportError:
        is_segment_locked = lambda _s: False  # type: ignore[assignment]

    if is_segment_locked(seg):
        budget.final_status = "overflow_locked" if budget.status == "overflow" else "underflow_locked"
        budget.rewrite_reason = f"{reason}:translation_lock"
        if budget.status == "overflow":
            try:
                from engines.pipeline_integrity.overflow_manager import register_overflow

                register_overflow(
                    seg,
                    index=idx,
                    overflow_ms=int(budget.overflow or 0),
                    slot_ms=int(budget.slot_duration or 0),
                    reason=str(reason),
                )
            except Exception:
                seg["overflow"] = True
                seg["slot_overflow"] = True
                seg["overflow_ms"] = int(budget.overflow or seg.get("overflow_ms") or 0)
            # Text rewrite blocked by LOCK. Audio chain may adapt later in slot_fit/ATO.
            # Until audio stamps executed=true, record explicit skip_reason.
            if not seg.get("adaptation_executed"):
                from engines.dub_engine_v2.adaptation_decision import (
                    SKIP_TRANSLATION_LOCKED,
                    mark_adaptation_skipped,
                )

                mark_adaptation_skipped(
                    seg,
                    skip_reason=SKIP_TRANSLATION_LOCKED,
                    index=idx,
                    overflow_ms=int(budget.overflow or 0),
                    need_adaptation=True,
                    decision="skip_text_rewrite",
                )
        elif budget.status == "underflow":
            try:
                from engines.pipeline_integrity.underflow_manager import register_underflow

                register_underflow(
                    seg,
                    index=idx,
                    shortfall_ms=int(budget.underflow or 0),
                    slot_ms=int(budget.slot_duration or 0),
                    audio_ms=int(budget.measured_duration or 0),
                    reason=str(reason),
                )
            except Exception:
                seg["underflow"] = True
            if not seg.get("adaptation_executed"):
                from engines.dub_engine_v2.adaptation_decision import (
                    SKIP_TRANSLATION_LOCKED,
                    mark_adaptation_skipped,
                )

                mark_adaptation_skipped(
                    seg,
                    skip_reason=SKIP_TRANSLATION_LOCKED,
                    index=idx,
                    underflow_ms=int(budget.underflow or 0),
                    need_adaptation=True,
                    decision="skip_text_rewrite",
                )
        logger.info(
            "closed_loop: TRANSLATION_LOCK blocks text rewrite idx=%s reason=%s "
            "skip_reason=%s adaptation_executed=%s "
            "(audio strategy chain continues in slot_fit/ATO)",
            idx,
            reason,
            seg.get("adaptation_skip_reason") or "",
            bool(seg.get("adaptation_executed")),
        )
        return _done(budget)

    adapt_direction: str | None = None
    max_iters = max(1, min(int(max_iterations or MAX_REWRITE_ITERATIONS), 5))

    for attempt in range(1, max_iters + 1):
        needs, reason = _needs_rewrite(budget)
        if not needs:
            break

        direction = "shrink" if reason == "duration_overflow" else "expand"
        if adapt_direction and direction != adapt_direction:
            # Anti-oscillation: accept current as good enough.
            budget.final_status = "accepted_anti_oscillation"
            break
        adapt_direction = direction

        original = _segment_text(seg)
        actual_ms = budget.measured_duration
        try:
            from engines.translation_adapt import set_llm_context

            set_llm_context(segment=idx, stage=f"closed_loop_{attempt}")
        except Exception:
            pass

        if direction == "shrink":
            opt = optimize_llm_rephrase_for_slot(
                original,
                source_hint=source_hint,
                slot_ms=budget.slot_duration,
                tgt_lang=target_lang,
                max_rounds=1,
                current_ms=actual_ms,
            )
            stage = "smart_compression"
            if not opt.changed:
                from engines.dsal import adapt_duration_semantic

                dsal = adapt_duration_semantic(
                    original,
                    source_hint=source_hint,
                    slot_ms=budget.slot_duration,
                    tgt_lang=target_lang,
                    actual_tts_ms=actual_ms,
                )
                if dsal.changed:
                    opt = type(opt)(
                        text=dsal.text,
                        changed=True,
                        budget=opt.budget,
                        stages=opt.stages,
                        stopped_reason="dsal_rule_compress",
                    )
                    stage = "dsal_rule_compress"
        else:
            opt = optimize_expand_for_slot(
                original,
                source_hint=source_hint,
                slot_ms=budget.slot_duration,
                tgt_lang=target_lang,
                max_rounds=1,
                current_ms=actual_ms,
            )
            stage = "smart_expansion"

        new_text = opt.text if opt.changed else original
        if not new_text or new_text.strip() == original:
            budget.rewrite_reason = f"{reason}:no_rewrite"
            # TZ v4.0: LLM unavailable ≠ hard fail when DSAL already tried
            if direction == "expand":
                seg["expand_required"] = True
            try:
                from engines.translation_adapt import llm_rephrase_available

                if not llm_rephrase_available():
                    seg["requires_llm_adaptation"] = False
                    budget.final_status = "dsal_exhausted_audio_fit"
                    if not seg.get("adaptation_executed"):
                        from engines.dub_engine_v2.adaptation_decision import (
                            SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED,
                            SKIP_NO_SEMANTIC_CANDIDATES,
                            mark_adaptation_skipped,
                        )

                        mark_adaptation_skipped(
                            seg,
                            skip_reason=(
                                SKIP_NO_SEMANTIC_CANDIDATES
                                if direction == "shrink"
                                else SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED
                            ),
                            index=idx,
                            overflow_ms=int(budget.overflow or 0),
                            underflow_ms=int(budget.underflow or 0),
                            need_adaptation=True,
                            decision="no_rewrite_llm_unavailable",
                        )
                else:
                    seg["requires_llm_adaptation"] = True
                    budget.final_status = "failed_no_rewrite"
                    if not seg.get("adaptation_executed"):
                        from engines.dub_engine_v2.adaptation_decision import (
                            SKIP_NO_SEMANTIC_CANDIDATES,
                            SKIP_DECISION_ENGINE_RETURNED_SKIP,
                            mark_adaptation_skipped,
                        )

                        mark_adaptation_skipped(
                            seg,
                            skip_reason=(
                                SKIP_NO_SEMANTIC_CANDIDATES
                                if direction == "shrink"
                                else SKIP_DECISION_ENGINE_RETURNED_SKIP
                            ),
                            index=idx,
                            overflow_ms=int(budget.overflow or 0),
                            underflow_ms=int(budget.underflow or 0),
                            need_adaptation=True,
                            decision="no_rewrite",
                        )
            except Exception:
                seg["requires_llm_adaptation"] = True
                budget.final_status = "failed_no_rewrite"
                if not seg.get("adaptation_executed"):
                    from engines.dub_engine_v2.adaptation_decision import (
                        SKIP_UNKNOWN,
                        mark_adaptation_skipped,
                    )

                    mark_adaptation_skipped(
                        seg,
                        skip_reason=SKIP_UNKNOWN,
                        index=idx,
                        overflow_ms=int(budget.overflow or 0),
                        underflow_ms=int(budget.underflow or 0),
                        need_adaptation=True,
                        decision="no_rewrite_error",
                    )
            break

        regen_result = regen_fn(
            new_text,
            voice=voice,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            task_id=task_id,
            segment_index=idx,
            segment_id=str(seg.get("segment_id") or ""),
        )
        if isinstance(regen_result, tuple):
            new_file, new_ms = regen_result[0], int(regen_result[1] or 0)
        else:
            new_file, new_ms = regen_result, 0

        if not new_file:
            budget.final_status = "failed_tts_regen"
            budget.rewrite_reason = reason
            break

        seg["text"] = new_text
        seg["plain_text"] = new_text
        seg["translation_text"] = new_text
        from engines.dub_engine_v2.adaptation_decision import mark_adaptation_executed

        mark_adaptation_executed(
            seg,
            decision=str(stage),
            stages=[str(stage)],
        )
        if str(stage).startswith("dsal"):
            seg["dsal_applied"] = True
        seg["file"] = new_file
        seg["tts_file_path"] = new_file
        if new_ms <= 0:
            new_ms = measure_actual_ms(seg, resolve_path=resolve_path)
        else:
            seg["playback_duration"] = new_ms
            seg["tts_ms"] = new_ms
            seg["actual_duration_ms"] = new_ms

        # Pause trim again after rewrite TTS
        pause_meta2 = apply_dynamic_pause_engine(
            seg,
            slot_ms=budget.slot_duration,
            work_dir=work_dir / "pause",
            resolve_path=resolve_path,
        )
        if pause_meta2.get("applied"):
            budget.pause_adjustments_ms += int(pause_meta2.get("pause_adjustments_ms") or 0)
            budget.pause_stages.extend(list(pause_meta2.get("stages") or []))

        if audit is not None:
            audit["tts_text"] = new_text
            audit["final_text"] = new_text
            qd = audit.setdefault("quality_details", {})
            qd["closed_loop"] = {
                "attempt": attempt,
                "reason": reason,
                "stage": stage,
                "text_before": original[:500],
                "text_after": new_text[:500],
                "tts_ms_before": actual_ms,
                "tts_ms_after": measure_actual_ms(seg, resolve_path=resolve_path),
            }

        if commit_fn:
            commit_fn(
                None,
                [idx],
                tts_text=new_text,
                audio_filename=str(seg.get("file") or new_file),
            )

        budget.rewrite_iterations = attempt
        budget.rewrite_reason = f"{reason}:{stage}"
        saved_pause = budget.pause_adjustments_ms
        saved_stages = list(budget.pause_stages or [])
        orig = budget.original_duration
        budget = build_timing_budget(seg, idx, timing_map)
        budget.rewrite_iterations = attempt
        budget.rewrite_reason = f"{reason}:{stage}"
        budget.pause_adjustments_ms = saved_pause
        budget.pause_stages = saved_stages
        budget.original_duration = orig or int(
            seg.get("first_tts_duration_ms") or budget.measured_duration
        )

        logger.info(
            "[ClosedLoop] task=%s seg=%d iter=%d reason=%s stage=%s "
            "actual=%dms slot=%dms score=%.1f",
            task_id,
            idx,
            attempt,
            reason,
            stage,
            budget.measured_duration,
            budget.slot_duration,
            budget.timing_score,
        )

        needs, reason = _needs_rewrite(budget)
        if not needs:
            budget.final_status = "ok"
            break
    else:
        needs, reason = _needs_rewrite(budget)
        budget.final_status = "ok" if not needs else "failed_max_iterations"
        if needs:
            seg["requires_llm_adaptation"] = True

    if budget.final_status == "pending":
        needs, _ = _needs_rewrite(budget)
        budget.final_status = "ok" if not needs else "failed"

    # Mandatory: never leave adaptation_executed=false without skip_reason
    if not seg.get("adaptation_executed"):
        from engines.dub_engine_v2.adaptation_decision import (
            SKIP_DECISION_ENGINE_RETURNED_SKIP,
            SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED,
            SKIP_UNKNOWN,
            finalize_segment_adaptation_fields,
            mark_adaptation_skipped,
        )

        if not str(seg.get("adaptation_skip_reason") or "").strip():
            if budget.final_status in ("failed_max_iterations", "failed", "failed_tts_regen"):
                try:
                    from engines.translation_adapt import llm_rephrase_available

                    reason = (
                        SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED
                        if not llm_rephrase_available()
                        else SKIP_DECISION_ENGINE_RETURNED_SKIP
                    )
                except Exception:
                    reason = SKIP_UNKNOWN
                mark_adaptation_skipped(
                    seg,
                    skip_reason=reason,
                    index=idx,
                    overflow_ms=int(budget.overflow or 0),
                    underflow_ms=int(budget.underflow or 0),
                    need_adaptation=True,
                    decision=str(budget.final_status),
                )
            else:
                finalize_segment_adaptation_fields(seg, index=idx)
    else:
        from engines.dub_engine_v2.adaptation_decision import finalize_segment_adaptation_fields

        finalize_segment_adaptation_fields(seg, index=idx)

    underflow_ms = max(0, int(budget.underflow or 0))
    slot_ms = max(0, int(budget.slot_duration or 0))
    if underflow_ms >= max(800, int(slot_ms * 0.12)):
        seg["expand_required"] = True
        seg["requires_llm_adaptation"] = True

    return _done(budget)


def run_closed_loop_timing(
    segments_data: list[dict],
    timing_map: list,
    *,
    source_segments: list[str],
    voice: str,
    target_lang: str,
    src_lang: str,
    work_dir: Path,
    regen_fn: Callable[..., Any] | None,
    commit_fn: Callable[..., Any] | None = None,
    audits: list[dict] | None = None,
    task_id: str | None = None,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    max_iterations: int | None = None,
    resolve_path: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Run Closed Loop Timing for every segment independently."""
    max_iters = int(
        max_iterations
        if max_iterations is not None
        else _env_int("VM_CLOSED_LOOP_MAX_ITERS", MAX_REWRITE_ITERATIONS)
    )
    # Allow 0 = pause-only Happy Path (no text rewrite). Previously max(1, …)
    # forced at least one rewrite attempt even when caller asked for pause-only.
    if max_iterations is not None and int(max_iterations) <= 0:
        max_iters = 0
    else:
        max_iters = max(1, min(max_iters, 5))

    try:
        from engines.translation_adapt import begin_llm_run

        begin_llm_run(task_id)
    except Exception:
        pass

    audit_by_idx = {int(a.get("index", -1)): a for a in (audits or [])}
    budgets: list[TimingBudget] = []
    stats: dict[str, Any] = {
        "engine": "closed_loop_timing",
        "checked": 0,
        "ok": 0,
        "rewritten": 0,
        "pause_only": 0,
        "failed": 0,
        "retries": 0,
        "deviations": 0,
        "fixed": 0,
        "adaptation_executed": False,
        "resegmented": 0,
        "issues": [],
        "avg_timing_score": 0.0,
        "segments_score_ge_95": 0,
    }

    # while-index: Adaptive Seg may insert a neighbor mid-pass (TZ §11)
    idx = 0
    while idx < len(segments_data):
        seg = segments_data[idx]
        if seg.get("merged_into") is not None:
            idx += 1
            continue
        if not (seg.get("file") or seg.get("tts_file_path")):
            idx += 1
            continue
        stats["checked"] += 1
        src_hint = source_segments[idx] if idx < len(source_segments) else ""
        before = build_timing_budget(seg, idx, timing_map)
        if before.status in ("overflow", "underflow"):
            stats["deviations"] += 1

        # Stage 19e: post-restore / predicted>>slot → forced split before mushy TTS fit.
        if segment_needs_stage19e_split(
            seg,
            slot_ms=int(before.slot_duration or 0),
            measured_ms=int(before.measured_duration or 0),
            lang=str(target_lang or "uk"),
        ):
            try:
                _src_list_e = (
                    source_segments
                    if isinstance(source_segments, list)
                    else list(source_segments or [])
                )
                _audits_mut_e = list(audits) if audits is not None else None
                import copy as _copy_s19e

                _parent_backup_e = _copy_s19e.deepcopy(seg)
                _parent_src_e = str(src_hint or "")
                _parent_timing_e = (
                    timing_map[idx]
                    if idx < len(timing_map)
                    else {"start": 0, "end": int(before.slot_duration or 0)}
                )
                if try_stage19e_post_restore_split(
                    segments_data=segments_data,
                    source_segments=_src_list_e,
                    timing_map=timing_map,
                    audits=_audits_mut_e,
                    idx=idx,
                    lang=str(target_lang or "uk"),
                ):
                    if isinstance(source_segments, list) and source_segments is not _src_list_e:
                        source_segments[:] = _src_list_e
                    if audits is not None and _audits_mut_e is not None:
                        audits.clear()
                        audits.extend(_audits_mut_e)
                    audit_by_idx = {
                        int(a.get("index", -1)): a for a in (audits or [])
                    }
                    n_children_e = int(
                        (segments_data[idx].get("stage19e") or {}).get(
                            "split_children"
                        )
                        or 0
                    )
                    if n_children_e < 2:
                        n_children_e = 1
                        while idx + n_children_e < len(segments_data):
                            meta = segments_data[idx + n_children_e].get("stage19e") or {}
                            if int(meta.get("split_parent_idx", -1)) != idx:
                                break
                            n_children_e += 1
                    regen_ok_all_e = True
                    if not callable(regen_fn):
                        regen_ok_all_e = False
                    else:
                        for _ri in range(idx, idx + n_children_e):
                            if _ri >= len(segments_data):
                                regen_ok_all_e = False
                                break
                            _s = segments_data[_ri]
                            _txt = str(
                                _s.get("plain_text") or _s.get("text") or ""
                            ).strip()
                            if not _txt or _text_looks_english(_txt):
                                regen_ok_all_e = False
                                break
                            try:
                                _stage21_watchdog_heartbeat(
                                    task_id, phase="stage21_split_regen", segment=_ri
                                )
                                _rr = regen_fn(
                                    _txt,
                                    voice=voice,
                                    tts_rate=tts_rate,
                                    tts_pitch=tts_pitch,
                                    task_id=task_id,
                                    segment_index=_ri,
                                    segment_id=str(_s.get("segment_id") or ""),
                                )
                                if isinstance(_rr, tuple):
                                    _nf, _nms = _rr[0], int(_rr[1] or 0)
                                else:
                                    _nf, _nms = _rr, 0
                                if not _nf:
                                    regen_ok_all_e = False
                                    break
                                _s["file"] = _nf
                                _s["tts_file_path"] = _nf
                                _s["final_tts_text"] = _txt
                                if _nms > 0:
                                    _s["playback_duration"] = _nms
                                    _s["tts_ms"] = _nms
                                    _s["actual_duration_ms"] = _nms
                                _stage21_watchdog_heartbeat(
                                    task_id, phase="stage21_split_regen_done", segment=_ri
                                )
                            except Exception as _rg_exc:
                                logger.warning(
                                    "closed_loop stage19e split regen failed idx=%s: %s",
                                    _ri,
                                    _rg_exc,
                                )
                                regen_ok_all_e = False
                                break
                    if not regen_ok_all_e:
                        # Stage 21: never roll back a clean structural split just
                        # because child TTS/network failed — keep children and
                        # mark re-TTS; rolling back re-creates parent overflow.
                        logger.warning(
                            "[Stage21] keep force-split seg#%d children=%d "
                            "despite regen failure (no rollback)",
                            idx,
                            n_children_e,
                        )
                        for _ri in range(idx, idx + n_children_e):
                            if _ri >= len(segments_data):
                                break
                            _s = segments_data[_ri]
                            _s["needs_re_tts"] = True
                            _s["force_split_executed"] = True
                            _s["split_executed"] = True
                            # Do not advertise "generated" without a WAV — handoff
                            # repair / Edge fallback must fill file before Studio.
                            if not (_s.get("file") or _s.get("tts_file_path")):
                                _s["status"] = "pending_regen"
                                _s["tts_status"] = "pending_regen"
                            meta21 = dict(_s.get("stage21") or {})
                            meta21.update(
                                {
                                    "force_split_executed": True,
                                    "split_executed": True,
                                    "final_status": "stage22_partial",
                                    "needs_re_tts": True,
                                }
                            )
                            _s["stage21"] = meta21
                            _s["stage22"] = {**dict(_s.get("stage22") or {}), **meta21}
                            _s["stage19j"] = {**dict(_s.get("stage19j") or {}), **meta21}
                        stats["resegmented"] += 1
                        stats["adaptation_executed"] = True
                        stats.setdefault("stage21_splits_kept_without_regen", 0)
                        stats["stage21_splits_kept_without_regen"] = (
                            int(stats["stage21_splits_kept_without_regen"]) + 1
                        )
                        if isinstance(source_segments, list) and source_segments is not _src_list_e:
                            source_segments[:] = _src_list_e
                        if audits is not None and _audits_mut_e is not None:
                            audits.clear()
                            audits.extend(_audits_mut_e)
                    else:
                        stats["resegmented"] += 1
                        stats["adaptation_executed"] = True
                        stats.setdefault("stage19e_splits", 0)
                        stats["stage19e_splits"] = int(stats["stage19e_splits"]) + 1
                        for _ri in range(idx, idx + n_children_e):
                            if _ri >= len(segments_data):
                                break
                            _s = segments_data[_ri]
                            if not (_s.get("file") or _s.get("tts_file_path")):
                                continue
                            _src = (
                                source_segments[_ri]
                                if _ri < len(source_segments)
                                else ""
                            )
                            _budget = run_closed_loop_segment(
                                _s,
                                _ri,
                                timing_map,
                                source_hint=str(_src or ""),
                                target_lang=target_lang,
                                src_lang=src_lang,
                                voice=voice,
                                work_dir=work_dir,
                                regen_fn=regen_fn,
                                commit_fn=commit_fn,
                                audit=audit_by_idx.get(_ri),
                                max_iterations=0,
                                tts_rate=tts_rate,
                                tts_pitch=tts_pitch,
                                task_id=task_id,
                                resolve_path=resolve_path,
                            )
                            budgets.append(_budget)
                            if _budget.final_status == "ok":
                                stats["ok"] += 1
                            elif int(_budget.rewrite_iterations or 0) > 0:
                                stats["rewritten"] += 1
                                stats["fixed"] += 1
                            elif _budget.pause_adjustments_ms > 0:
                                stats["pause_only"] += 1
                            else:
                                stats["failed"] += 1
                        idx += n_children_e
                        continue
            except Exception as _s19e_exc:
                logger.debug("closed_loop stage19e split skipped: %s", _s19e_exc)

        # Stage 19c §C: large overflow split (Happy Path included — not LLM-gated).
        if (
            before.status == "overflow"
            and large_overflow_needs_split(
                overflow_ms=int(before.overflow or 0),
                slot_ms=int(before.slot_duration or 0),
            )
            and not seg.get("stage19c_split_done")
            and not seg.get("adaptive_resegment_done")
        ):
            try:
                _src_list = (
                    source_segments
                    if isinstance(source_segments, list)
                    else list(source_segments or [])
                )
                _audits_mut = list(audits) if audits is not None else None
                import copy as _copy_s19c

                _parent_backup = _copy_s19c.deepcopy(seg)
                _parent_src = str(src_hint or "")
                _parent_timing = (
                    timing_map[idx]
                    if idx < len(timing_map)
                    else {"start": 0, "end": int(before.slot_duration or 0)}
                )
                if try_stage19c_overflow_split(
                    segments_data=segments_data,
                    source_segments=_src_list,
                    timing_map=timing_map,
                    audits=_audits_mut,
                    idx=idx,
                    overflow_ms=int(before.overflow or 0),
                ):
                    if isinstance(source_segments, list) and source_segments is not _src_list:
                        source_segments[:] = _src_list
                    if audits is not None and _audits_mut is not None:
                        audits.clear()
                        audits.extend(_audits_mut)
                    audit_by_idx = {
                        int(a.get("index", -1)): a for a in (audits or [])
                    }
                    n_children = int(
                        (segments_data[idx].get("stage19c") or {}).get(
                            "split_children"
                        )
                        or 0
                    )
                    if n_children < 2:
                        n_children = 1
                        while idx + n_children < len(segments_data):
                            meta = segments_data[idx + n_children].get("stage19c") or {}
                            if int(meta.get("split_parent_idx", -1)) != idx:
                                break
                            n_children += 1
                    logger.info(
                        "closed_loop: stage19c overflow split idx=%s children=%s",
                        idx,
                        n_children,
                    )
                    # Mandatory re-TTS for EVERY child — never leave active orphans.
                    regen_ok_all = True
                    if not callable(regen_fn):
                        regen_ok_all = False
                    else:
                        for _ri in range(idx, idx + n_children):
                            if _ri >= len(segments_data):
                                regen_ok_all = False
                                break
                            _s = segments_data[_ri]
                            _txt = str(
                                _s.get("plain_text") or _s.get("text") or ""
                            ).strip()
                            if not _txt or _text_looks_english(_txt):
                                regen_ok_all = False
                                break
                            try:
                                _rr = regen_fn(
                                    _txt,
                                    voice=voice,
                                    tts_rate=tts_rate,
                                    tts_pitch=tts_pitch,
                                    task_id=task_id,
                                    segment_index=_ri,
                                    segment_id=str(_s.get("segment_id") or ""),
                                )
                                if isinstance(_rr, tuple):
                                    _nf, _nms = _rr[0], int(_rr[1] or 0)
                                else:
                                    _nf, _nms = _rr, 0
                                if not _nf:
                                    regen_ok_all = False
                                    break
                                _s["file"] = _nf
                                _s["tts_file_path"] = _nf
                                _s["final_tts_text"] = _txt
                                if _nms > 0:
                                    _s["playback_duration"] = _nms
                                    _s["tts_ms"] = _nms
                                    _s["actual_duration_ms"] = _nms
                            except Exception as _rg_exc:
                                logger.warning(
                                    "closed_loop stage19c split regen failed idx=%s: %s",
                                    _ri,
                                    _rg_exc,
                                )
                                regen_ok_all = False
                                break

                    if not regen_ok_all:
                        rollback_stage19c_split(
                            segments_data=segments_data,
                            source_segments=_src_list
                            if isinstance(_src_list, list)
                            else source_segments,
                            timing_map=timing_map,
                            audits=_audits_mut if audits is not None else None,
                            idx=idx,
                            n_children=n_children,
                            parent_backup=_parent_backup,
                            parent_src=_parent_src,
                            parent_timing=_parent_timing,
                        )
                        if isinstance(source_segments, list) and source_segments is not _src_list:
                            source_segments[:] = _src_list
                        if audits is not None and _audits_mut is not None:
                            audits.clear()
                            audits.extend(_audits_mut)
                        # Fall through to normal closed-loop on restored parent.
                    else:
                        stats["resegmented"] += 1
                        stats["adaptation_executed"] = True
                        stats.setdefault("stage19c_splits", 0)
                        stats["stage19c_splits"] = int(stats["stage19c_splits"]) + 1
                        for _ri in range(idx, idx + n_children):
                            if _ri >= len(segments_data):
                                break
                            _s = segments_data[_ri]
                            if not (_s.get("file") or _s.get("tts_file_path")):
                                continue
                            _src = (
                                source_segments[_ri]
                                if _ri < len(source_segments)
                                else ""
                            )
                            _budget = run_closed_loop_segment(
                                _s,
                                _ri,
                                timing_map,
                                source_hint=str(_src or ""),
                                target_lang=target_lang,
                                src_lang=src_lang,
                                voice=voice,
                                work_dir=work_dir,
                                regen_fn=regen_fn,
                                commit_fn=commit_fn,
                                audit=audit_by_idx.get(_ri),
                                max_iterations=0,  # no LLM; Stage 19c rule text-fit still runs
                                tts_rate=tts_rate,
                                tts_pitch=tts_pitch,
                                task_id=task_id,
                                resolve_path=resolve_path,
                            )
                            budgets.append(_budget)
                            if _budget.final_status == "ok":
                                stats["ok"] += 1
                            elif int(_budget.rewrite_iterations or 0) > 0:
                                stats["rewritten"] += 1
                                stats["fixed"] += 1
                            elif _budget.pause_adjustments_ms > 0:
                                stats["pause_only"] += 1
                            else:
                                stats["failed"] += 1
                        idx += n_children
                        continue
            except Exception as _s19c_exc:
                logger.debug("closed_loop stage19c split skipped: %s", _s19c_exc)

        # TZ §11: adaptive resegment (advanced path) before aggressive shorten.
        # Happy Path: skip — Stage 19c split above covers large overflow.
        _allow_resegment = True
        try:
            from engines.happy_path import (
                advanced_adaptation_enabled,
                task_info_for,
            )

            _hp_info = task_info_for(task_id) if task_id else {}
            _allow_resegment = bool(advanced_adaptation_enabled(_hp_info))
        except Exception:
            _allow_resegment = True
        if (
            _allow_resegment
            and before.status == "overflow"
            and not seg.get("adaptive_resegment_done")
        ):
            try:
                from engines.adaptive_segmentation.post_tts import (
                    should_prefer_resegment,
                    try_split_long_overflow_segment,
                )
                from engines.adaptive_segmentation.retranslate import (
                    apply_retranslate_if_needed,
                )

                if should_prefer_resegment(
                    slot_ms=int(before.slot_duration or 0),
                    tts_ms=int(before.measured_duration or 0),
                    overflow_ms=int(before.overflow or 0),
                ):
                    _audits_mut = list(audits) if audits is not None else None
                    if try_split_long_overflow_segment(
                        segments_data=segments_data,
                        source_segments=source_segments
                        if isinstance(source_segments, list)
                        else list(source_segments or []),
                        timing_map=timing_map,
                        audits=_audits_mut,
                        idx=idx,
                    ):
                        if audits is not None and _audits_mut is not None:
                            audits.clear()
                            audits.extend(_audits_mut)
                        audit_by_idx = {
                            int(a.get("index", -1)): a for a in (audits or [])
                        }
                        stats["resegmented"] += 1
                        stats["adaptation_executed"] = True
                        logger.info(
                            "closed_loop: adaptive resegment before shorten idx=%s",
                            idx,
                        )
                        # Retranslate + TTS for both halves; skip shorten this pass
                        for _ri in (idx, idx + 1):
                            if _ri >= len(segments_data):
                                continue
                            _s = segments_data[_ri]
                            _src = (
                                source_segments[_ri]
                                if _ri < len(source_segments)
                                else ""
                            )
                            apply_retranslate_if_needed(
                                _s,
                                str(_src or ""),
                                src_lang=src_lang,
                                tgt_lang=target_lang,
                            )
                            _txt = str(
                                _s.get("plain_text") or _s.get("text") or ""
                            ).strip()
                            if _txt and callable(regen_fn):
                                try:
                                    _rr = regen_fn(
                                        _txt,
                                        voice=voice,
                                        tts_rate=tts_rate,
                                        tts_pitch=tts_pitch,
                                        task_id=task_id,
                                        segment_index=_ri,
                                        segment_id=str(_s.get("segment_id") or ""),
                                    )
                                    if isinstance(_rr, tuple):
                                        _nf, _nms = _rr[0], int(_rr[1] or 0)
                                    else:
                                        _nf, _nms = _rr, 0
                                    if _nf:
                                        _s["file"] = _nf
                                        _s["tts_file_path"] = _nf
                                        if _nms > 0:
                                            _s["playback_duration"] = _nms
                                            _s["tts_ms"] = _nms
                                except Exception as _rg_exc:
                                    logger.debug(
                                        "closed_loop resegment regen failed: %s",
                                        _rg_exc,
                                    )
                        # TZ §11: after resegment+retranslate+TTS, skip shorten
                        # this pass — only pause-fit / re-measure on both halves.
                        for _ri in (idx, idx + 1):
                            if _ri >= len(segments_data):
                                continue
                            _s = segments_data[_ri]
                            _src = (
                                source_segments[_ri]
                                if _ri < len(source_segments)
                                else ""
                            )
                            _budget = run_closed_loop_segment(
                                _s,
                                _ri,
                                timing_map,
                                source_hint=str(_src or ""),
                                target_lang=target_lang,
                                src_lang=src_lang,
                                voice=voice,
                                work_dir=work_dir,
                                regen_fn=regen_fn,
                                commit_fn=commit_fn,
                                audit=audit_by_idx.get(_ri),
                                # No LLM loops; Stage 19b/19c rule text-fit still runs (§G).
                                max_iterations=0,
                                tts_rate=tts_rate,
                                tts_pitch=tts_pitch,
                                task_id=task_id,
                                resolve_path=resolve_path,
                            )
                            budgets.append(_budget)
                            if _budget.final_status == "ok":
                                stats["ok"] += 1
                            elif int(_budget.rewrite_iterations or 0) > 0:
                                stats["rewritten"] += 1
                                stats["fixed"] += 1
                            elif _budget.pause_adjustments_ms > 0:
                                stats["pause_only"] += 1
                            else:
                                stats["failed"] += 1
                        idx += 2
                        continue
            except Exception as _as_loop_exc:
                logger.debug(
                    "closed_loop adaptive resegment skipped: %s", _as_loop_exc
                )

        budget = run_closed_loop_segment(
            seg,
            idx,
            timing_map,
            source_hint=src_hint,
            target_lang=target_lang,
            src_lang=src_lang,
            voice=voice,
            work_dir=work_dir,
            regen_fn=regen_fn,
            commit_fn=commit_fn,
            audit=audit_by_idx.get(idx),
            max_iterations=max_iters,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            task_id=task_id,
            resolve_path=resolve_path,
        )
        # Stage 19e: anti-truncate may restore a giant Final mid-pass — split now.
        if seg.get("needs_post_restore_split") and not seg.get("stage19e_split_done"):
            try:
                _src_list_e2 = (
                    source_segments
                    if isinstance(source_segments, list)
                    else list(source_segments or [])
                )
                _audits_mut_e2 = list(audits) if audits is not None else None
                import copy as _copy_s19e2

                _parent_backup_e2 = _copy_s19e2.deepcopy(seg)
                _parent_src_e2 = (
                    source_segments[idx] if idx < len(source_segments) else src_hint
                )
                _parent_timing_e2 = (
                    timing_map[idx]
                    if idx < len(timing_map)
                    else {"start": 0, "end": int(budget.slot_duration or 0)}
                )
                if try_stage19e_post_restore_split(
                    segments_data=segments_data,
                    source_segments=_src_list_e2,
                    timing_map=timing_map,
                    audits=_audits_mut_e2,
                    idx=idx,
                    lang=str(target_lang or "uk"),
                ):
                    if isinstance(source_segments, list) and source_segments is not _src_list_e2:
                        source_segments[:] = _src_list_e2
                    if audits is not None and _audits_mut_e2 is not None:
                        audits.clear()
                        audits.extend(_audits_mut_e2)
                    audit_by_idx = {
                        int(a.get("index", -1)): a for a in (audits or [])
                    }
                    n_e2 = int(
                        (segments_data[idx].get("stage19e") or {}).get("split_children")
                        or 0
                    )
                    if n_e2 < 2:
                        n_e2 = 1
                        while idx + n_e2 < len(segments_data):
                            meta = segments_data[idx + n_e2].get("stage19e") or {}
                            if int(meta.get("split_parent_idx", -1)) != idx:
                                break
                            n_e2 += 1
                    regen_ok_e2 = bool(callable(regen_fn))
                    if regen_ok_e2:
                        for _ri in range(idx, idx + n_e2):
                            if _ri >= len(segments_data):
                                regen_ok_e2 = False
                                break
                            _s = segments_data[_ri]
                            _txt = str(
                                _s.get("plain_text") or _s.get("text") or ""
                            ).strip()
                            if not _txt or _text_looks_english(_txt):
                                regen_ok_e2 = False
                                break
                            try:
                                _rr = regen_fn(
                                    _txt,
                                    voice=voice,
                                    tts_rate=tts_rate,
                                    tts_pitch=tts_pitch,
                                    task_id=task_id,
                                    segment_index=_ri,
                                    segment_id=str(_s.get("segment_id") or ""),
                                )
                                if isinstance(_rr, tuple):
                                    _nf, _nms = _rr[0], int(_rr[1] or 0)
                                else:
                                    _nf, _nms = _rr, 0
                                if not _nf:
                                    regen_ok_e2 = False
                                    break
                                _s["file"] = _nf
                                _s["tts_file_path"] = _nf
                                _s["final_tts_text"] = _txt
                                if _nms > 0:
                                    _s["playback_duration"] = _nms
                                    _s["tts_ms"] = _nms
                                    _s["actual_duration_ms"] = _nms
                            except Exception:
                                regen_ok_e2 = False
                                break
                    if not regen_ok_e2:
                        # Stage 21: keep structural split; do not restore overflow parent.
                        logger.warning(
                            "[Stage21] keep force-split (post-pass) seg#%d "
                            "children=%d despite regen failure",
                            idx,
                            n_e2,
                        )
                        for _ri in range(idx, idx + n_e2):
                            if _ri >= len(segments_data):
                                break
                            _s = segments_data[_ri]
                            _s["needs_re_tts"] = True
                            _s["force_split_executed"] = True
                            _s["split_executed"] = True
                            if not (_s.get("file") or _s.get("tts_file_path")):
                                _s["status"] = "pending_regen"
                                _s["tts_status"] = "pending_regen"
                            meta21 = dict(_s.get("stage21") or {})
                            meta21.update(
                                {
                                    "force_split_executed": True,
                                    "split_executed": True,
                                    "final_status": "stage22_partial",
                                    "needs_re_tts": True,
                                }
                            )
                            _s["stage21"] = meta21
                            _s["stage22"] = {**dict(_s.get("stage22") or {}), **meta21}
                            _s["stage19j"] = {**dict(_s.get("stage19j") or {}), **meta21}
                        stats["resegmented"] += 1
                        stats["adaptation_executed"] = True
                        stats.setdefault("stage21_splits_kept_without_regen", 0)
                        stats["stage21_splits_kept_without_regen"] = (
                            int(stats["stage21_splits_kept_without_regen"]) + 1
                        )
                        budgets.append(budget)
                    else:
                        stats["resegmented"] += 1
                        stats["adaptation_executed"] = True
                        stats.setdefault("stage19e_splits", 0)
                        stats["stage19e_splits"] = int(stats["stage19e_splits"]) + 1
                        for _ri in range(idx, idx + n_e2):
                            if _ri >= len(segments_data):
                                break
                            _s = segments_data[_ri]
                            if not (_s.get("file") or _s.get("tts_file_path")):
                                continue
                            _src = (
                                source_segments[_ri]
                                if _ri < len(source_segments)
                                else ""
                            )
                            _budget = run_closed_loop_segment(
                                _s,
                                _ri,
                                timing_map,
                                source_hint=str(_src or ""),
                                target_lang=target_lang,
                                src_lang=src_lang,
                                voice=voice,
                                work_dir=work_dir,
                                regen_fn=regen_fn,
                                commit_fn=commit_fn,
                                audit=audit_by_idx.get(_ri),
                                max_iterations=0,
                                tts_rate=tts_rate,
                                tts_pitch=tts_pitch,
                                task_id=task_id,
                                resolve_path=resolve_path,
                            )
                            budgets.append(_budget)
                            if _budget.final_status == "ok":
                                stats["ok"] += 1
                            elif int(_budget.rewrite_iterations or 0) > 0:
                                stats["rewritten"] += 1
                                stats["fixed"] += 1
                            else:
                                stats["failed"] += 1
                        idx += n_e2
                        continue
            except Exception as _s19e2_exc:
                logger.debug(
                    "closed_loop stage19e post-pass split skipped: %s", _s19e2_exc
                )

        budgets.append(budget)
        stats["retries"] += int(budget.rewrite_iterations)
        if budget.rewrite_iterations > 0:
            stats["rewritten"] += 1
            stats["adaptation_executed"] = True
            stats["fixed"] += 1
        elif budget.pause_adjustments_ms > 0 and budget.final_status == "ok":
            stats["pause_only"] += 1
        if budget.final_status == "ok":
            stats["ok"] += 1
        else:
            stats["failed"] += 1
            stats["issues"].append(
                {
                    "idx": idx,
                    "code": budget.status,
                    "final_status": budget.final_status,
                    "tts_ms": budget.measured_duration,
                    "slot_ms": budget.slot_duration,
                    "overflow_ms": budget.overflow,
                    "underflow_ms": budget.underflow,
                    "rewrite_reason": budget.rewrite_reason,
                }
            )
        idx += 1

    scores = [b.timing_score for b in budgets]
    stats["avg_timing_score"] = round(sum(scores) / len(scores), 1) if scores else 0.0
    stats["segments_score_ge_95"] = sum(1 for s in scores if s >= TIMING_SCORE_GOAL)
    stats["budgets"] = [b.to_dict() for b in budgets]

    # Surface LLM gate for strict mode (same contract as post_tts_validate_and_retry)
    needing = [
        b.index
        for b in budgets
        if b.final_status.startswith("failed")
        or (b.status in ("overflow", "underflow") and b.final_status != "ok")
    ]
    if needing:
        stats["requires_llm_adaptation"] = {
            "count": len(needing),
            "segment_indices": needing,
            "reason": "closed_loop_unresolved",
        }

    return stats


def validate_timeline(
    segments_data: list[dict],
    timing_map: list,
) -> dict[str, Any]:
    """Final pass: overlap / gap / speech-overlap without shifting neighbors."""
    overlaps: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    speech_overlaps: list[dict[str, Any]] = []

    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        if idx >= len(timing_map):
            continue
        start_ms, end_ms = _parse_timing(timing_map[idx])
        actual = measure_actual_ms(seg)
        speech_end = start_ms + actual
        # Slot map overlaps
        if idx + 1 < len(timing_map):
            next_start, next_end = _parse_timing(timing_map[idx + 1])
            map_overlap = end_ms - next_start
            if map_overlap > OVERLAP_TOLERANCE_MS:
                overlaps.append(
                    {
                        "index": idx,
                        "code": "segment_overlap",
                        "overlap_ms": map_overlap,
                    }
                )
            gap = next_start - end_ms
            if gap > 700:
                gaps.append({"index": idx, "code": "long_gap", "gap_ms": gap})
            speech_ov = speech_end - next_start
            if actual > 0 and speech_ov > OVERLAP_TOLERANCE_MS:
                speech_overlaps.append(
                    {
                        "index": idx,
                        "code": "speech_overlap",
                        "overlap_ms": speech_ov,
                        "speech_end_ms": speech_end,
                        "next_start_ms": next_start,
                    }
                )

    ok = not overlaps and not speech_overlaps
    return {
        "ok": ok,
        "segment_overlap": overlaps,
        "speech_overlap": speech_overlaps,
        "gap_analysis": gaps,
        "problem_indices": sorted(
            {
                *(o["index"] for o in overlaps),
                *(s["index"] for s in speech_overlaps),
            }
        ),
    }


def build_timing_report(
    segments_data: list[dict],
    timing_map: list,
    *,
    closed_loop_stats: dict[str, Any] | None = None,
    timeline_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build timing_report.json payload."""
    rows: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        budget = seg.get("timing_budget")
        if not isinstance(budget, dict):
            budget = build_timing_budget(seg, idx, timing_map).to_dict()
        cl = seg.get("closed_loop") or {}
        rows.append(
            {
                "index": idx,
                "original_duration": budget.get("original_duration")
                or seg.get("first_tts_duration_ms")
                or budget.get("measured_duration"),
                "tts_duration": budget.get("measured_duration")
                or budget.get("tts_duration"),
                "slot_duration": budget.get("slot_duration"),
                "slot_start": budget.get("slot_start"),
                "slot_end": budget.get("slot_end"),
                "delta": budget.get("delta"),
                "overflow": budget.get("overflow"),
                "underflow": budget.get("underflow"),
                "rewrite_iterations": budget.get("rewrite_iterations")
                or cl.get("iterations")
                or 0,
                "pause_adjustments": budget.get("pause_adjustments_ms")
                or cl.get("pause_adjustments_ms")
                or 0,
                "pause_stages": budget.get("pause_stages") or [],
                "timing_score": budget.get("timing_score") or seg.get("timing_score") or 0,
                "final_status": budget.get("final_status") or cl.get("final_status") or "unknown",
                "rewrite_reason": budget.get("rewrite_reason") or cl.get("rewrite_reason") or "",
                "status": budget.get("status"),
            }
        )

    scores = [float(r.get("timing_score") or 0) for r in rows]
    return {
        "engine": "closed_loop_timing",
        "segment_count": len(rows),
        "avg_timing_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "segments_score_ge_95": sum(1 for s in scores if s >= TIMING_SCORE_GOAL),
        "kpi_target_score": TIMING_SCORE_GOAL,
        "overflow_threshold_ms": OVERFLOW_THRESHOLD_MS,
        "underflow_threshold_ms": UNDERFLOW_THRESHOLD_MS,
        "max_rewrite_iterations": MAX_REWRITE_ITERATIONS,
        "closed_loop": closed_loop_stats or {},
        "timeline_validation": timeline_validation or {},
        "segments": rows,
    }


def write_timing_report(
    report: dict[str, Any],
    *,
    app_dir: Path,
    task_id: str,
) -> Path:
    out_dir = app_dir / "output" / "diagnostics" / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "timing_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Also mirror next to output root for easy discovery
    mirror = app_dir / "output" / f"timing_report_{task_id[:8]}.json"
    try:
        mirror.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    return path


def allow_block_merge() -> bool:
    """Cascade neighbor shift is last resort — off by default."""
    return _env_bool("VM_ALLOW_BLOCK_MERGE", False)
