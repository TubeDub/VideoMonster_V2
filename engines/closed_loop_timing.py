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
UNDERFLOW_THRESHOLD_MS = 450
OVERLAP_TOLERANCE_MS = 40
TIMING_SCORE_GOAL = 95


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

            # Do not hard-force False — duration delta >500ms must keep need=True
            _need = resolve_need_adaptation(
                seg,
                need_adaptation=False,
                overflow_ms=int(budget.overflow or 0),
                underflow_ms=int(budget.underflow or 0),
            )
            mark_adaptation_skipped(
                seg,
                skip_reason=SKIP_FITS_NO_CHANGE,
                index=idx,
                overflow_ms=int(budget.overflow or 0),
                underflow_ms=int(budget.underflow or 0),
                need_adaptation=_need,
                decision="fits_no_change" if not _need else "duration_delta_needs_adapt",
            )
        seg["timing_budget"] = budget.to_dict()
        seg["timing_score"] = budget.timing_score
        return budget

    # max_iterations<=0 → pause-only (used after Adaptive Seg resegment, TZ §11)
    if int(max_iterations or 0) <= 0:
        budget.final_status = "ok" if not needs else "deferred_after_resegment"
        budget.rewrite_reason = "pause_only_after_resegment"
        seg["timing_budget"] = budget.to_dict()
        seg["timing_score"] = budget.timing_score
        return budget

    if regen_fn is None:
        budget.final_status = "failed_no_regen"
        budget.rewrite_reason = reason
        seg["timing_budget"] = budget.to_dict()
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
        return budget

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
        seg["timing_budget"] = budget.to_dict()
        seg["timing_score"] = budget.timing_score
        logger.info(
            "closed_loop: TRANSLATION_LOCK blocks text rewrite idx=%s reason=%s "
            "skip_reason=%s adaptation_executed=%s "
            "(audio strategy chain continues in slot_fit/ATO)",
            idx,
            reason,
            seg.get("adaptation_skip_reason") or "",
            bool(seg.get("adaptation_executed")),
        )
        return budget

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
        "underflow_ms": underflow_ms,
        "expand_required": bool(seg.get("expand_required")),
        "adaptation_skip_reason": seg.get("adaptation_skip_reason") or "",
        "adaptation_decision": seg.get("adaptation_decision") or {},
    }
    return budget


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

        # TZ §11: resegment oversized overflow before any text shorten
        if before.status == "overflow" and not seg.get("adaptive_resegment_done"):
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
                                max_iterations=0,  # pause only — no text shorten
                                tts_rate=tts_rate,
                                tts_pitch=tts_pitch,
                                task_id=task_id,
                                resolve_path=resolve_path,
                            )
                            budgets.append(_budget)
                            if _budget.final_status == "ok":
                                stats["ok"] += 1
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
