"""
Timing-Aware Translation — adapt translated text to segment slot before TTS.

Priority: natural shorter phrasing that fits the slot; never rely on TTS speed-up here.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

logger = logging.getLogger("tubedub.timing_aware_translation")

WORD_TOLERANCE = 2
MAX_ITERATIONS = 4
_SLOT_PADDING_MS = 40


def _adaptation_watchdog_timeout() -> float:
    """Per-segment watchdog ceiling.

    Derived from the segment's OWN adaptation budget so the watchdog never cuts
    a segment before it exhausts its independent budget (ТЗ P0). In
    "max_quality" mode the budget is unlimited, so the watchdog is very large
    (quality first). An explicit env override always wins.
    """
    try:
        v = float(os.getenv("VM_AI_ADAPTATION_TIMEOUT_SEC", "") or "")
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    try:
        from engines.translation_adapt import (
            MODE_MAX_QUALITY,
            adaptation_speed_mode,
            per_segment_budget_s,
        )

        if adaptation_speed_mode() == MODE_MAX_QUALITY:
            # Quality-first, but STILL finite (P0 DoD: never hang forever).
            return 600.0
        seg_budget = per_segment_budget_s()
        if seg_budget > 0:
            # Allow the segment its whole budget plus headroom for scoring/IO,
            # capped so one hard segment can never stall the whole run.
            return min(300.0, max(120.0, seg_budget * 3.0 + 60.0))
    except Exception:
        pass
    return 180.0


@dataclass
class TimingAwareRecord:
    index: int
    source_words: int = 0
    input_words: int = 0
    output_words: int = 0
    slot_ms: int = 0
    predicted_ms_before: int = 0
    predicted_ms_after: int = 0
    iterations: int = 0
    text_before: str = ""
    text_after: str = ""
    adapted: bool = False
    reason: str = ""
    final_tts_ms: int = 0
    delta_ms: int = 0
    meaning_loss_score: float = 0.0
    entity_preservation_score: float = 1.0
    optimization_stages: list = field(default_factory=list)
    information_removed: bool = False
    requires_llm_adaptation: bool = False
    llm_called: bool = False
    llm_skip_reason: str = ""
    ai_adaptation_trace: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def word_count(text: str) -> int:
    return len(str(text or "").split())


_TERMINAL_PUNCT = ".!?…»\"')"


def _looks_truncated(original: str, adapted: str) -> bool:
    """Stricter truncation/incompleteness check (TZ §1).

    Flags ellipsis clips, mid-clause cut-offs, AND adaptations that drop the
    sentence's terminal punctuation (i.e. an unfinished sentence).
    """
    from engines.semantic_meaning import is_truncated_adaptation

    orig = str(original or "").strip()
    adpt = str(adapted or "").strip()
    if not adpt or adpt == orig:
        return False
    if is_truncated_adaptation(orig, adpt):
        return True
    # If the original ended as a complete sentence but the adaptation does not,
    # and it lost words, treat it as an incomplete (clipped) sentence.
    orig_complete = orig[-1:] in _TERMINAL_PUNCT
    adpt_complete = adpt[-1:] in _TERMINAL_PUNCT
    if orig_complete and not adpt_complete and word_count(adpt) >= 4:
        return True
    return False


def slot_ms_from_timing(timing_map: Sequence[Any] | None, index: int) -> int:
    if not timing_map or index < 0 or index >= len(timing_map):
        return 0
    entry = timing_map[index]
    try:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            start_ms = int(entry[0])
            end_ms = int(entry[1])
        elif isinstance(entry, dict):
            start_ms = int(entry.get("start", 0))
            end_ms = int(entry.get("end", 0))
        else:
            return 0
        return max(0, end_ms - start_ms)
    except (TypeError, ValueError):
        return 0


def adapt_segment_to_slot(
    text: str,
    *,
    source_text: str,
    slot_ms: int,
    src_lang: str,
    tgt_lang: str,
    index: int = 0,
    word_tolerance: int = WORD_TOLERANCE,
    max_iterations: int = MAX_ITERATIONS,
) -> tuple[str, TimingAwareRecord]:
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.semantic_optimizer import compute_time_budget

    original = " ".join(str(text or "").split())
    budget = compute_time_budget(original, slot_ms, tgt_lang=tgt_lang)
    record = TimingAwareRecord(
        index=index,
        source_words=word_count(source_text),
        input_words=word_count(original),
        output_words=word_count(original),
        slot_ms=int(slot_ms or 0),
        predicted_ms_before=budget.tts_estimated_ms,
        predicted_ms_after=budget.tts_estimated_ms,
        text_before=original,
        text_after=original,
        delta_ms=budget.delta_ms,
    )

    if not original or slot_ms <= 0:
        record.reason = "no_slot_or_empty"
        return original, record

    if budget.fits:
        # Bidirectional (TZ §3/§4): if the dubbed line is much shorter than the
        # ORIGINAL ENGLISH speech, try a natural longer rephrase to better match
        # the source duration. Target = source duration (not the raw slot, which
        # may be long due to pauses). No-op without an LLM (never pad with fillers).
        from engines.translation_adapt import llm_rephrase_available

        if llm_rephrase_available() and source_text.strip():
            from engines.semantic_optimizer import EXPAND_TRIGGER_RATIO, optimize_expand_for_slot

            source_est = estimate_tts_duration_ms(source_text, src_lang or tgt_lang)
            too_short = (
                source_est > 0
                and budget.tts_estimated_ms < int(source_est * EXPAND_TRIGGER_RATIO)
                and budget.tts_estimated_ms < int(budget.target_ms * EXPAND_TRIGGER_RATIO)
            )
            if too_short:
                exp = optimize_expand_for_slot(
                    original,
                    source_hint=source_text,
                    slot_ms=slot_ms,
                    tgt_lang=tgt_lang,
                )
                if exp.changed:
                    record.text_after = exp.text
                    record.output_words = word_count(exp.text)
                    record.predicted_ms_after = estimate_tts_duration_ms(exp.text, tgt_lang)
                    record.adapted = True
                    record.reason = exp.stopped_reason
                    record.iterations = len([s for s in exp.stages if s.applied])
                    record.meaning_loss_score = exp.meaning_loss_score
                    record.entity_preservation_score = exp.entity_preservation_score
                    record.optimization_stages = [s.to_dict() for s in exp.stages]
                    record.delta_ms = exp.budget.delta_ms
                    return exp.text, record
                record.reason = exp.stopped_reason or "fits_no_change"
                return original, record
        record.reason = "fits_no_change"
        return original, record

    opt = None
    from engines.ai_adaptation_engine import adapt_segment_ai

    ai_result = adapt_segment_ai(
        original,
        source_hint=source_text,
        raw_translation=original,
        slot_ms=slot_ms,
        tgt_lang=tgt_lang,
        index=index,
    )
    adapted_text = ai_result.text
    opt_changed = ai_result.changed
    record.requires_llm_adaptation = ai_result.requires_llm_adaptation
    record.llm_called = ai_result.llm_called
    record.llm_skip_reason = ai_result.trace.llm_skip_reason
    record.ai_adaptation_trace = ai_result.trace.to_dict()

    # Hard anti-truncation guarantee (TZ §1): never emit a clipped / unfinished
    # sentence. If detected, revert to the full grammatical line and let the
    # audio stage (gap-absorb / video-adapt) handle any overflow.
    if (
        adapted_text
        and adapted_text != original
        and _looks_truncated(original, adapted_text)
    ):
        logger.warning(
            "[TimingAware] idx=%d rejected truncated adaptation, keeping full sentence",
            index,
        )
        adapted_text = original
        opt_changed = False
        ai_result.stopped_reason = "rejected_truncation_kept_full"

    record.text_after = adapted_text
    record.output_words = word_count(adapted_text)
    record.predicted_ms_after = estimate_tts_duration_ms(adapted_text, tgt_lang)
    record.adapted = opt_changed
    record.reason = ai_result.stopped_reason
    record.iterations = ai_result.trace.iterations
    record.meaning_loss_score = 1.0 - ai_result.trace.meaning_score if ai_result.trace.meaning_score else 0.0
    record.entity_preservation_score = ai_result.trace.meaning_score or 1.0
    record.optimization_stages = ai_result.trace.stages
    record.information_removed = False
    from engines.semantic_optimizer import compute_time_budget

    _final_budget = compute_time_budget(adapted_text, slot_ms, tgt_lang=tgt_lang)
    record.delta_ms = _final_budget.delta_ms

    return adapted_text, record


def _resolve_worker_count(speed_mode: str | None, total: int) -> int:
    """Auto-size the parallel worker pool from CPU resources (Task 5).

    Actual LLM network calls are serialized by the gateway semaphore, so extra
    workers accelerate the many rule-only segments without overloading a single
    local model. An explicit env override always wins.
    """
    try:
        env = int(os.getenv("VM_ADAPT_MAX_WORKERS", "") or "")
        if env > 0:
            return min(env, max(1, total))
    except (TypeError, ValueError):
        pass
    cpu = os.cpu_count() or 2
    workers = max(1, min(cpu, 4))
    return min(workers, max(1, total))


def adapt_segments_to_timing(
    segments: list[str],
    timing_map: Sequence[Any] | None,
    source_segments: list[str] | None,
    *,
    src_lang: str,
    tgt_lang: str,
    task_id: str = "",
    raw_mt_segments: list[str] | None = None,
    speed_mode: str | None = None,
    per_segment_budget_s: float | None = None,
    project_budget_s: float | None = None,
    progress_cb=None,
) -> tuple[list[str], list[TimingAwareRecord]]:
    """Adapt each segment translation to its timing slot.

    ``progress_cb`` (optional) is called as ``progress_cb(done, total)`` before
    each segment so the UI can show "Адаптация текста... Сегмент N из M"
    (ТЗ §5). ``speed_mode`` / ``per_segment_budget_s`` / ``project_budget_s``
    configure the INDEPENDENT per-segment budget for this run (ТЗ §1/§3).
    """
    from engines.translation_stage_log import log_translation_stage

    # Configure the per-segment adaptation budget for this dub run. Each segment
    # gets its OWN independent budget — a hard segment never skips the others
    # (ТЗ P0). Never a single project-wide timer that skips later segments.
    try:
        from engines.translation_adapt import begin_llm_run

        begin_llm_run(
            task_id,
            mode=speed_mode,
            per_segment_s=per_segment_budget_s,
            project_s=project_budget_s,
        )
    except Exception:
        pass

    total = len(segments)

    src_rows = list(source_segments or [])
    raw_rows = list(raw_mt_segments or [])
    out: list[str | None] = [None] * total
    records: list[TimingAwareRecord | None] = [None] * total

    from engines.pipeline_segment_watchdog import run_segment_bounded

    def _watchdog_fallback_text(
        current: str,
        *,
        index: int,
        source_hint: str,
    ) -> tuple[str, str]:
        """On watchdog failure prefer Raw MT — never English source leak."""
        from engines.pipeline_language_gate import is_critical_language_mismatch

        cur = str(current or "").strip()
        raw = str(raw_rows[index] if index < len(raw_rows) else "").strip()
        if cur:
            bad, _ = is_critical_language_mismatch(
                cur, target_lang=tgt_lang, original=source_hint
            )
            if not bad:
                return cur, "watchdog_keep_input"
        if raw:
            bad_raw, _ = is_critical_language_mismatch(
                raw, target_lang=tgt_lang, original=source_hint
            )
            if not bad_raw:
                return raw, "watchdog_fallback_raw_mt"
            return raw, "watchdog_fallback_raw_mt_unverified"
        return cur, "watchdog_no_safe_fallback"

    def _process_segment(i: int, text: str) -> TimingAwareRecord:
        slot_ms = slot_ms_from_timing(timing_map, i)
        source_hint = src_rows[i] if i < len(src_rows) else ""
        original = str(text or "")
        try:
            from engines.repetition_guard import remove_repeated_sentences

            deduped, rep_fixed = remove_repeated_sentences(original)
            if rep_fixed:
                original = deduped
        except Exception:
            pass

        def _run_adapt() -> tuple[str, TimingAwareRecord]:
            # Attribute LLM calls to this segment INSIDE the worker/watchdog
            # thread so thread-local context is correct under parallelism.
            try:
                from engines.translation_adapt import set_llm_context

                set_llm_context(segment=i, stage="timing_aware")
            except Exception:
                pass
            return adapt_segment_to_slot(
                original,
                source_text=source_hint,
                slot_ms=slot_ms,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                index=i,
            )

        def _fallback_adapt() -> tuple[str, TimingAwareRecord]:
            fb_text, fb_reason = _watchdog_fallback_text(
                original, index=i, source_hint=source_hint
            )
            fb_rec = TimingAwareRecord(
                index=i,
                source_words=word_count(source_hint),
                input_words=word_count(original),
                output_words=word_count(fb_text),
                slot_ms=int(slot_ms or 0),
                text_before=original,
                text_after=fb_text,
                reason=fb_reason,
            )
            return fb_text, fb_rec

        # Watchdog (Task 2): every segment has its OWN finite timer; on timeout
        # it cancels and uses a safe fallback so the run can never hang forever.
        watch = run_segment_bounded(
            task_id=task_id,
            phase="timing_aware_translation",
            segment_index=i,
            fn=_run_adapt,
            fallback=_fallback_adapt,
            timeout_sec=_adaptation_watchdog_timeout(),
        )
        adapted, rec = watch.value
        if watch.timed_out or watch.error:
            rec.reason = f"watchdog_{watch.error or 'timeout'}"
        out[i] = adapted
        records[i] = rec
        return rec

    # ── Progress with strategy + ETA (Task 8) ────────────────────────────
    import threading as _threading

    _prog_lock = _threading.Lock()
    _prog = {"done": 0, "t0": time.monotonic()}

    def _strategy_label(rec: TimingAwareRecord) -> str:
        trace = getattr(rec, "ai_adaptation_trace", None) or {}
        sclass = str(trace.get("strategy_class") or "")
        if sclass == "full_llm":
            return "LLM Rewrite"
        if sclass == "rule_rewrite":
            return "Semantic Rewrite"
        reason = str(getattr(rec, "reason", "") or "")
        if reason.startswith("expand"):
            return "Timing Rewrite"
        if sclass == "none" or reason in ("fits_no_change", "fits"):
            return "—"
        return "Timing Rewrite"

    def _report(rec: TimingAwareRecord) -> None:
        if progress_cb is None:
            return
        with _prog_lock:
            _prog["done"] += 1
            done = _prog["done"]
            elapsed = time.monotonic() - _prog["t0"]
        eta_s = None
        if done > 0 and done < total:
            eta_s = max(0.0, (elapsed / done) * (total - done))
        try:
            progress_cb(done, total, strategy=_strategy_label(rec), eta_s=eta_s)
        except TypeError:
            try:
                progress_cb(done, total)  # backward-compatible callback
            except Exception:
                pass
        except Exception:
            pass

    # ── Parallel processing (Task 5) ─────────────────────────────────────
    # Independent segments are adapted concurrently. Actual LLM network calls
    # are serialized by the gateway semaphore (queue), so a single local model
    # is never overloaded while rule-only segments finish fully in parallel.
    workers = _resolve_worker_count(speed_mode, total)

    if workers <= 1 or total <= 1:
        for i, text in enumerate(segments):
            rec = _process_segment(i, text)
            _report(rec)
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aicore-adapt") as ex:
            futures = {ex.submit(_process_segment, i, text): i for i, text in enumerate(segments)}
            from concurrent.futures import as_completed

            for fut in as_completed(futures):
                try:
                    rec = fut.result()
                except Exception:
                    i = futures[fut]
                    rec = TimingAwareRecord(index=i, text_before=str(segments[i] or ""),
                                            text_after=str(segments[i] or ""), reason="worker_error")
                    out[i] = str(segments[i] or "")
                    records[i] = rec
                _report(rec)

    # Fill any gaps defensively and finalize ordered outputs.
    adapted_count = 0
    for i in range(total):
        if records[i] is None:
            records[i] = TimingAwareRecord(index=i, text_before=str(segments[i] or ""),
                                           text_after=str(segments[i] or ""), reason="missing")
            out[i] = str(segments[i] or "")
        if out[i] is None:
            out[i] = str(segments[i] or "")
        rec = records[i]
        if rec.adapted:
            adapted_count += 1
        log_translation_stage(
            task_id or None,
            stage="post_timing_aware_translation",
            segment_index=i,
            text=out[i],
            source_lang=src_lang,
            target_lang=tgt_lang,
            changed=rec.adapted,
            detail=(
                f"slot_ms={rec.slot_ms} pred={rec.predicted_ms_after}ms "
                f"words={rec.input_words}->{rec.output_words} reason={rec.reason}"
            ),
        )

    logger.info(
        "[TimingAware] task=%s segments=%d adapted=%d workers=%d",
        task_id or "?",
        len(segments),
        adapted_count,
        workers,
    )
    return [str(x or "") for x in out], [r for r in records if r is not None]


def apply_records_to_audits(
    audits: list[dict[str, Any]],
    records: list[TimingAwareRecord],
) -> None:
    """Write timing-aware results into translation audit rows for UI/OpenDDF."""
    by_idx = {int(a.get("index", -1)): a for a in audits}
    for rec in records:
        row = by_idx.get(rec.index)
        if not row:
            continue
        row["final_text"] = rec.text_after
        row["tts_text"] = rec.text_after
        row["semantic_text"] = rec.text_after
        qd = dict(row.get("quality_details") or {})
        qd["timing_aware"] = rec.to_dict()
        row["quality_details"] = qd
        if rec.adapted:
            row["timing_aware_adapted"] = True
