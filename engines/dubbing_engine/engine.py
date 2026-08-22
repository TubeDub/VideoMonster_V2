"""
DubbingEngine — Unified 7-stage professional dubbing pipeline.

Pipeline per segment:
  Stage 1  Entity Context      Recognise & protect named entities
  Stage 2  Text Adaptation     Natural rephrasing (SSO → ADA → feedback loop)
  Stage 3  Punctuation         Restore sentence-ending marks for TTS pauses
  Stage 4  Stress Marks        Unicode accents for natural intonation (UK/RU)
  Stage 5  Voice Quality Gate  Re-adapt if projected atempo > threshold
  Stage 6  Timing Coordination No overlap, block-merge when needed
  Stage 7  Validation Gate     8-point pre-TTS check; skip_tts if critical fail

Integration point: replaces SSO + ADA + prepare_segments_for_tts chain
in auto_dub_api._run_pipeline_inner.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from engines.dubbing_engine.types import (
    DubbingResult,
    DubbingSegment,
    EntityInfo,
    StageLog,
)

logger = logging.getLogger("tubedub.dubbing_engine")

# ── Fallback constants (overridden by ModeProfile when mode is set) ────────────
_MAX_ADAPT_ROUNDS: int = 3
_CHARS_PER_SEC: dict[str, float] = {
    "ru": 14.0,
    "uk": 13.5,
    "de": 12.5,
    "en": 16.0,
}
_DEFAULT_CHARS_PER_SEC: float = 13.5
_SLOT_TOLERANCE_PCT: float = 10.0
_MAX_ATEMPO: float = 1.15
_VIDEO_ADAPT_THRESHOLD: float = 1.10
_NATURAL_PAUSE_MS: int = 160


def _predict_ms(text: str, lang: str) -> int:
    """Syllable-based TTS duration estimator (replaces char/CPS estimate)."""
    try:
        from engines.dubbing_engine.predictor import predict_ms

        return predict_ms(text, lang)
    except Exception:
        base = (lang or "ru").split("-")[0].lower()
        cps = _CHARS_PER_SEC.get(base, _DEFAULT_CHARS_PER_SEC)
        return max(100, int(len(text.strip()) / cps * 1000))


def _slot_from_entry(entry: Any) -> tuple[int, int]:
    """Return (start_ms, end_ms) from a timing_map entry."""
    if isinstance(entry, dict):
        return int(entry.get("start", 0)), int(entry.get("end", 0))
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        return int(entry[0]), int(entry[1])
    return 0, 0


class DubbingEngine:
    """
    Unified 7-stage dubbing engine — single entry point for all pre-TTS preparation.

    Usage:
        engine = DubbingEngine(lang="uk", app_dir=APP_DIR, task_id=task_id,
                               content_mode="movie")
        results = engine.process_all(segments, timing_map, source_hints=sources)
        tts_segments = [r.output_text for r in results if r.passed_validation]
    """

    def __init__(
        self,
        lang: str = "uk",
        app_dir: Path | None = None,
        task_id: str = "",
        content_mode: str = "movie",
        skip_text_adaptation: bool = False,
        adapt_fn: Callable[..., str] | None = None,
        duration_adapt_fn: Callable[..., str] | None = None,
    ) -> None:
        self.lang = lang
        self.app_dir = app_dir or Path(__file__).resolve().parent.parent.parent
        self.task_id = task_id
        self._run_id = uuid.uuid4().hex[:8]
        self._skip_text_adaptation = bool(skip_text_adaptation)
        # Freeze P1: adapters are injected by Translation layer — Dub Engine
        # must not import translation_adapt / SSO / ADA / ai_core itself.
        self._adapt_fn = adapt_fn
        self._duration_adapt_fn = duration_adapt_fn

        # Load mode profile
        try:
            from engines.dubbing_engine.content_mode import get_profile, ContentMode

            self._profile = get_profile(ContentMode.from_str(content_mode))
        except Exception:
            self._profile = None  # fallback to defaults

        # Apply profile overrides to instance-level constants
        p = self._profile
        self._slot_tolerance_pct: float = (
            p.slot_tolerance_pct if p else _SLOT_TOLERANCE_PCT
        )
        self._max_atempo: float = p.max_atempo if p else _MAX_ATEMPO
        self._video_adapt_threshold: float = p.video_adapt_threshold if p else 10.0
        self._min_word_retention: float = p.min_word_retention if p else 0.60
        self._strict_pause: bool = p.strict_pause_preservation if p else False
        self._allow_merge: bool = p.allow_merge if p else True
        self._min_silence_preserve_ms: int = p.min_silence_preserve_ms if p else 500

        logger.info(
            "[DubEngine] mode=%s atempo=%.2f slot_tol=%.0f%% strict_pause=%s",
            content_mode,
            self._max_atempo,
            self._slot_tolerance_pct,
            self._strict_pause,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def process_all(
        self,
        segments: list[str],
        final_audio_from_dubbing_engine: list[Any],
        source_hints: list[str] | None = None,
        natural_pauses_out: list[int] | None = None,
        segment_ids: list[str] | None = None,
    ) -> list[DubbingResult]:
        """
        Process every segment through the 7-stage pipeline.

        Args:
            segments:    Translated segments (target language).
            timing_map:  [{start, end}] timeline entries.
            source_hints: Original-language (source) text per segment.
            natural_pauses_out: If provided, filled with natural_pause_ms per segment.

        Returns:
            DubbingResult list — one per input segment.
            Segments that fail validation have passed_validation=False and
            recommended_strategy="skip_tts".
        """
        t0 = time.perf_counter()
        results: list[DubbingResult] = []
        prev_end_ms: int = 0  # tracks previous segment's end for overlap check

        n = len(segments)
        for i, seg_text in enumerate(segments):
            src_hint = source_hints[i] if source_hints and i < len(source_hints) else ""
            start_ms, end_ms = (0, 0)
            if final_audio_from_dubbing_engine and i < len(
                final_audio_from_dubbing_engine
            ):
                start_ms, end_ms = _slot_from_entry(final_audio_from_dubbing_engine[i])
            slot_ms = max(0, end_ms - start_ms)

            from engines.pipeline_segment_watchdog import run_segment_bounded

            seg_input = str(seg_text or "").strip()

            def _run_segment() -> DubbingResult:
                return self._process_segment(
                    index=i,
                    translated_text=seg_input,
                    source_text=src_hint,
                    slot_start_ms=start_ms,
                    slot_end_ms=end_ms,
                    prev_end_ms=prev_end_ms,
                    segment_id=(
                        str(segment_ids[i]).strip()
                        if segment_ids and i < len(segment_ids)
                        else ""
                    ),
                )

            def _fallback_segment() -> DubbingResult:
                return DubbingResult(
                    index=i,
                    original_text=src_hint,
                    input_text=seg_input,
                    output_text=seg_input,
                    passed_validation=bool(seg_input),
                    validation_notes=["segment_watchdog_timeout"],
                    predicted_ms=_predict_ms(seg_input, self.lang),
                    slot_ms=slot_ms,
                    recommended_strategy="direct",
                    segment_id=(
                        str(segment_ids[i]).strip()
                        if segment_ids and i < len(segment_ids)
                        else ""
                    ),
                )

            watch = run_segment_bounded(
                task_id=self.task_id,
                phase="dubbing_engine",
                segment_index=i,
                fn=_run_segment,
                fallback=_fallback_segment,
            )
            result = watch.value
            if watch.timed_out or watch.error:
                result.validation_notes = list(result.validation_notes or [])
                result.validation_notes.append(
                    f"watchdog_{watch.error or 'timeout'}"
                )
            results.append(result)
            # Track end time for next segment's overlap check
            prev_end_ms = end_ms if end_ms > 0 else prev_end_ms

            if natural_pauses_out is not None:
                natural_pauses_out.append(result.natural_pause_ms)

        elapsed = time.perf_counter() - t0
        self._write_report(results, elapsed)
        logger.info(
            "[DubEngine] task=%s %d segs: %d adapted | %d skipped | %.2fs",
            self.task_id,
            n,
            sum(
                1
                for r in results
                if r.recommended_strategy not in ("direct", "skip_tts")
            ),
            sum(1 for r in results if not r.passed_validation),
            elapsed,
        )
        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Per-segment pipeline
    # ──────────────────────────────────────────────────────────────────────────

    def _process_segment(
        self,
        index: int,
        translated_text: str,
        source_text: str,
        slot_start_ms: int,
        slot_end_ms: int,
        prev_end_ms: int,
        segment_id: str = "",
    ) -> DubbingResult:
        slot_ms = max(0, slot_end_ms - slot_start_ms)
        stage_log: list[StageLog] = []
        current = str(translated_text or "").strip()
        input_text = current

        # ── Stage 1: Entity Context ───────────────────────────────────────────
        entities = self._stage_entities(source_text, current, stage_log)

        # ── Stage 2: Text Adaptation ─────────────────────────────────────────
        if self._skip_text_adaptation:
            stage_log.append(
                StageLog(
                    stage="adapt",
                    applied=False,
                    before=current,
                    after=current,
                    note="skipped_pre_timed",
                )
            )
        else:
            current = self._stage_adapt(
                current, slot_ms, source_text, entities, stage_log, round_=1
            )

        # ── Stage 3: Punctuation ─────────────────────────────────────────────
        current = self._stage_punctuation(current, stage_log)

        # ── Stage 4: Stress Marks ────────────────────────────────────────────
        stress_applied = False
        current, stress_applied = self._stage_stress(current, stage_log)

        # ── Stage 4.5: Phonetic Entity Resolver ──────────────────────────────
        # Fix pronunciation of foreign proper nouns (Fiat→Фіат, BMW→Бі Ем Ве)
        current = self._stage_phonetics(current, stage_log)

        # ── Stage 5: Voice Quality Gate ───────────────────────────────────────
        # If projected atempo > threshold, re-adapt (up to _MAX_ADAPT_ROUNDS)
        if self._skip_text_adaptation:
            stage_log.append(
                StageLog(
                    stage="voice",
                    applied=False,
                    before=current,
                    after=current,
                    note="skipped_pre_timed",
                )
            )
        else:
            current = self._stage_voice_quality_gate(
                current, slot_ms, source_text, entities, stage_log
            )

        # ── Stage 6: Timing Coordination ─────────────────────────────────────
        strategy, timing_note = self._stage_timing(
            current, slot_ms, slot_start_ms, slot_end_ms, prev_end_ms, stage_log
        )

        # ── Stage 7: Validation Gate ─────────────────────────────────────────
        predicted_ms = _predict_ms(current, self.lang)
        from engines.dubbing_engine.validation import run_validation

        vreport = run_validation(
            input_text=input_text,
            output_text=current,
            source_text=source_text,
            entities=entities,
            stress_applied=stress_applied,
            punct_ok=True,
            predicted_ms=predicted_ms,
            slot_ms=slot_ms,
            prev_end_ms=prev_end_ms,
            slot_start_ms=slot_start_ms,
            lang=self.lang,
        )
        stage_log.append(
            StageLog(
                stage="validate",
                applied=True,
                before=current,
                after=current,
                note=f"passed={vreport.passed} checks={list(vreport.checks.keys())}",
            )
        )

        # Merge timing strategy + validation strategy
        final_strategy = strategy
        if not vreport.passed and vreport.strategy == "skip_tts":
            final_strategy = "skip_tts"
        elif vreport.strategy == "adapt_more" and strategy == "direct":
            final_strategy = "video_adapt"  # can't adapt more → ask video

        natural_pause = 120
        try:
            from engines.dubbing_engine.punctuation import terminal_pause_ms

            natural_pause = terminal_pause_ms(current)
        except Exception:
            pass

        result = DubbingResult(
            index=index,
            original_text=source_text,
            input_text=input_text,
            output_text=current,
            passed_validation=vreport.passed or final_strategy != "skip_tts",
            validation_notes=vreport.notes,
            stage_log=stage_log,
            predicted_ms=predicted_ms,
            slot_ms=slot_ms,
            natural_pause_ms=natural_pause,
            recommended_strategy=final_strategy,
            entity_ok=vreport.checks.get("entity", True),
            punct_ok=vreport.checks.get("punct", True),
            stress_ok=vreport.checks.get("stress", True),
            timing_ok=vreport.checks.get("timing", True),
            voice_ok=vreport.checks.get("voice", True),
            lang_ok=vreport.checks.get("lang", True),
            meaning_ok=vreport.checks.get("meaning", True),
            segment_id=str(segment_id or "").strip(),
        )
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Stage implementations
    # ──────────────────────────────────────────────────────────────────────────

    def _stage_entities(
        self,
        source_text: str,
        translated_text: str,
        stage_log: list[StageLog],
    ) -> list[EntityInfo]:
        """Stage 1: extract and verify named entities."""
        t0 = time.perf_counter()
        entities: list[EntityInfo] = []
        corrected = translated_text
        notes: list[str] = []
        try:
            from engines.dubbing_engine.entities import (
                extract_entities,
                protect_entities_in_translation,
            )

            entities = extract_entities(source_text, self.lang)
            if entities:
                corrected, notes = protect_entities_in_translation(
                    source_text, translated_text, entities, self.lang
                )
        except Exception as exc:
            logger.debug("[DubEngine] entity stage error: %s", exc)

        applied = corrected != translated_text or bool(notes)
        stage_log.append(
            StageLog(
                stage="entity",
                applied=applied,
                before=translated_text,
                after=corrected,
                note=f"found={len(entities)} restored={len(notes)}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        )
        return entities

    def _stage_adapt(
        self,
        text: str,
        slot_ms: int,
        source_hint: str,
        entities: list[EntityInfo],
        stage_log: list[StageLog],
        round_: int = 1,
    ) -> str:
        """Stage 2: text adaptation via injected Translation-layer adapter (Freeze P1)."""
        t0 = time.perf_counter()
        if not text or slot_ms <= 0:
            stage_log.append(
                StageLog(
                    stage="adapt",
                    applied=False,
                    before=text,
                    after=text,
                    note="no_slot_or_empty",
                )
            )
            return text

        predicted = _predict_ms(text, self.lang)
        # Use syllable-based PASS threshold (< 15% overflow → no changes)
        try:
            from engines.dubbing_engine.predictor import PASS_THRESHOLD

            pass_limit = slot_ms * PASS_THRESHOLD
        except Exception:
            pass_limit = slot_ms * 1.15

        if predicted <= pass_limit:
            stage_log.append(
                StageLog(
                    stage="adapt",
                    applied=False,
                    before=text,
                    after=text,
                    note=(
                        f"PASS: predicted={predicted}ms slot={slot_ms}ms "
                        f"ratio={predicted/slot_ms:.2f} (<{PASS_THRESHOLD:.0%})"
                    ),
                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                )
            )
            return text

        overflow_pct = (predicted / slot_ms - 1.0) * 100
        adapted = text
        method = "none"

        # Freeze P1: text adaptation only via injected Translation-layer adapter.
        # Dub Engine must not import SSO / ADA / translation_adapt.
        if self._adapt_fn is not None:
            try:
                candidate = self._adapt_fn(
                    text,
                    slot_ms=slot_ms,
                    source_hint=source_hint,
                    lang=self.lang,
                    round_=round_,
                )
                if candidate and candidate != text:
                    adapted = str(candidate)
                    method = "injected_adapt"
            except Exception as exc:
                logger.debug("[DubEngine] injected adapt error: %s", exc)

        applied = adapted != text
        stage_log.append(
            StageLog(
                stage="adapt",
                applied=applied,
                before=text,
                after=adapted,
                note=(
                    f"round={round_} method={method} overflow={overflow_pct:.1f}% "
                    f"pred_before={predicted}ms pred_after={_predict_ms(adapted, self.lang)}ms"
                ),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        )
        return adapted

    def _stage_punctuation(self, text: str, stage_log: list[StageLog]) -> str:
        """Stage 3: restore sentence-ending punctuation."""
        t0 = time.perf_counter()
        try:
            from engines.dubbing_engine.punctuation import restore_punctuation

            fixed, changes = restore_punctuation(text)
        except Exception as exc:
            logger.debug("[DubEngine] punctuation stage error: %s", exc)
            fixed, changes = text, []

        stage_log.append(
            StageLog(
                stage="punct",
                applied=bool(changes),
                before=text,
                after=fixed,
                note=",".join(changes) or "ok",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        )
        return fixed

    def _stage_stress(
        self,
        text: str,
        stage_log: list[StageLog],
    ) -> tuple[str, bool]:
        """Stage 4: add Unicode stress marks for UK/RU."""
        t0 = time.perf_counter()
        base_lang = (self.lang or "uk").split("-")[0].lower()
        applied = False
        result_text = text
        if base_lang in ("uk", "ru", "be"):
            try:
                from engines.stress_marks import add_stress_marks

                result_text = add_stress_marks(text, lang=base_lang)
                applied = result_text != text
            except Exception as exc:
                logger.debug("[DubEngine] stress stage error: %s", exc)

        stage_log.append(
            StageLog(
                stage="stress",
                applied=applied,
                before=text,
                after=result_text,
                note="ok" if applied else "skipped",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        )
        return result_text, applied

    def _stage_phonetics(self, text: str, stage_log: list[StageLog]) -> str:
        """
        Stage 4.5: Phonetic Entity Resolver.

        Replaces foreign proper nouns / acronyms with TTS-friendly forms
        so the voice engine pronounces them correctly.
        Examples: "Fiat" → "Фіат",  "BMW" → "Бі Ем Ве",  "George" → "Джордж".
        """
        t0 = time.perf_counter()
        try:
            from engines.dubbing_engine.phonetics import resolve_phonetics

            resolved, changes = resolve_phonetics(text, self.lang)
        except Exception as exc:
            logger.debug("[Engine] phonetics stage skipped: %s", exc)
            resolved, changes = text, []

        stage_log.append(
            StageLog(
                stage="phonetics",
                applied=bool(changes),
                before=text,
                after=resolved,
                note=(f"fixed: {', '.join(changes)}" if changes else "no_changes"),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        )
        if changes:
            logger.info("[Engine] Phonetic fixes: %s", ", ".join(changes))
        return resolved

    def _stage_voice_quality_gate(
        self,
        text: str,
        slot_ms: int,
        source_hint: str,
        entities: list[EntityInfo],
        stage_log: list[StageLog],
    ) -> str:
        """
        Stage 5: if projected atempo > _MAX_ATEMPO, try harder text adaptation.
        Re-runs punctuation after each adaptation round.
        If adaptation would lose too many words (retention < 60%), reject it and
        let the timing stage recommend video_adapt instead.
        """
        if slot_ms <= 0:
            return text

        def _word_retention(original: str, adapted: str) -> float:
            orig_words = {
                w.lower().strip(".,!?;:—–") for w in original.split() if len(w) > 2
            }
            ada_words = {
                w.lower().strip(".,!?;:—–") for w in adapted.split() if len(w) > 2
            }
            if not orig_words:
                return 1.0
            return len(orig_words & ada_words) / len(orig_words)

        def _restore_punct(t: str) -> str:
            try:
                from engines.dubbing_engine.punctuation import restore_punctuation

                fixed, _ = restore_punctuation(t)
                return fixed
            except Exception:
                return t

        current = text
        applied_any = False
        for round_ in range(1, _MAX_ADAPT_ROUNDS + 1):
            predicted = _predict_ms(current, self.lang)
            ratio = predicted / slot_ms if slot_ms else 1.0
            if ratio <= self._max_atempo:
                break
            t0 = time.perf_counter()
            logger.debug(
                "[DubEngine] Stage 5 round %d: atempo_proj=%.2f > %.2f — re-adapting",
                round_,
                ratio,
                self._max_atempo,
            )
            improved = False
            if self._duration_adapt_fn is not None:
                try:
                    target_ms = int(slot_ms * _MAX_ATEMPO * 0.92)
                    candidate = self._duration_adapt_fn(
                        current,
                        predicted,
                        target_ms,
                        source_hint,
                        lang=self.lang,
                    )
                    if candidate and candidate != current:
                        retention = _word_retention(text, candidate)
                        if retention >= self._min_word_retention:
                            candidate = _restore_punct(str(candidate))
                            stage_log.append(
                                StageLog(
                                    stage="voice",
                                    applied=True,
                                    before=current,
                                    after=candidate,
                                    note=(
                                        f"round={round_} atempo={ratio:.2f}→re-adapted "
                                        f"retention={retention:.2f}"
                                    ),
                                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                                )
                            )
                            current = candidate
                            applied_any = True
                            improved = True
                        else:
                            logger.debug(
                                "[DubEngine] Stage 5 round %d: retention=%.2f < 0.60 — reject",
                                round_,
                                retention,
                            )
                except Exception as exc:
                    logger.debug("[DubEngine] Stage 5 adapt error: %s", exc)
            if not improved:
                # No safe adaptation — signal timing stage to use video_adapt
                stage_log.append(
                    StageLog(
                        stage="voice",
                        applied=False,
                        before=current,
                        after=current,
                        note=f"atempo_proj={ratio:.2f} — no safe adapt; will use video_adapt",
                    )
                )
                break

        if not applied_any and not stage_log:
            stage_log.append(
                StageLog(
                    stage="voice",
                    applied=False,
                    before=current,
                    after=current,
                    note="ok",
                )
            )

        return current

    def _stage_timing(
        self,
        text: str,
        slot_ms: int,
        slot_start_ms: int,
        slot_end_ms: int,
        prev_end_ms: int,
        stage_log: list[StageLog],
    ) -> tuple[str, str]:
        """
        Stage 6: timing coordination — STRICT no-overlap enforcement.

        Rules (per ТЗ):
        • A segment MUST NOT start before the previous segment ends.
        • After punctuation (. ! ?) a natural pause must be preserved.
        • If segment overflows: first text_adapt, then merge_next (max 2 blocks),
          only then minimal video_adapt.
        • strategy="overlap_blocked" signals caller to push start time forward.

        Returns (strategy, note).
        """
        strategy = "direct"
        notes: list[str] = []
        predicted = _predict_ms(text, self.lang)

        # ── Overlap detection (strict: 0ms tolerance) ──────────────────────────
        if slot_start_ms > 0 and prev_end_ms > 0:
            gap_ms = slot_start_ms - prev_end_ms
            if gap_ms < 0:
                overlap_ms = -gap_ms
                notes.append(
                    f"OVERLAP DETECTED: start={slot_start_ms}ms "
                    f"prev_end={prev_end_ms}ms overlap={overlap_ms}ms"
                )
                strategy = "overlap_blocked"
                logger.warning(
                    "[Engine] Seg overlap detected: slot_start=%dms prev_end=%dms "
                    "overlap=%dms — forcing delay_start",
                    slot_start_ms,
                    prev_end_ms,
                    overlap_ms,
                )
            elif gap_ms < 80:
                # Too short a gap (< 80ms) — natural pause too small
                notes.append(f"gap_too_small={gap_ms}ms (<80ms)")
                strategy = "add_pause"

        # ── Overflow analysis ──────────────────────────────────────────────────
        if slot_ms > 0 and predicted > 0:
            overflow_ratio = predicted / slot_ms
            overflow_pct = (overflow_ratio - 1.0) * 100.0
            if overflow_ratio <= 1.0 + self._slot_tolerance_pct / 100:
                # Within tolerance — fine
                if strategy == "direct":
                    strategy = "direct"
            elif overflow_pct < 15.0:
                # < 15% overflow → try text_adapt first, then video_adapt
                strategy = "video_adapt"
                notes.append(f"overflow={overflow_pct:.1f}% (<15%) → video_adapt")
            elif overflow_pct < 25.0 and self._allow_merge:
                # 15-25% → merge preferred over deletion
                strategy = "merge_next"
                notes.append(f"overflow={overflow_pct:.1f}% (15-25%) → merge_next")
            elif self._allow_merge:
                strategy = "merge_next"
                notes.append(f"overflow={overflow_pct:.1f}% (>25%) → merge_next")
            else:
                strategy = "video_adapt"
                notes.append(
                    f"overflow={overflow_pct:.1f}% → video_adapt (merge disabled)"
                )

        stage_log.append(
            StageLog(
                stage="timing",
                applied=strategy not in ("direct",),
                before=text,
                after=text,
                note=(
                    f"strategy={strategy} pred={predicted}ms slot={slot_ms}ms "
                    f"prev_end={prev_end_ms}ms "
                )
                + ("; ".join(notes) if notes else "ok"),
            )
        )
        return strategy, "; ".join(notes)

    # ──────────────────────────────────────────────────────────────────────────
    # Reporting
    # ──────────────────────────────────────────────────────────────────────────

    def _write_report(self, results: list[DubbingResult], elapsed: float) -> None:
        """Write JSON + human-readable report to output/dev/dubbing_engine/."""
        try:
            report_dir = self.app_dir / "output" / "dev" / "dubbing_engine"
            report_dir.mkdir(parents=True, exist_ok=True)

            # JSON machine-readable report
            payload: dict[str, Any] = {
                "task_id": self.task_id,
                "run_id": self._run_id,
                "lang": self.lang,
                "elapsed_sec": round(elapsed, 3),
                "segments_total": len(results),
                "segments_adapted": sum(
                    1
                    for r in results
                    if r.recommended_strategy not in ("direct", "skip_tts")
                ),
                "segments_skipped": sum(1 for r in results if not r.passed_validation),
                "strategies": {
                    s: sum(1 for r in results if r.recommended_strategy == s)
                    for s in (
                        "direct",
                        "adapted",
                        "video_adapt",
                        "merge_next",
                        "skip_tts",
                        "delay_start",
                    )
                },
                "segments": [r.to_dict() for r in results],
            }
            json_path = report_dir / f"engine_{self._run_id}.json"
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (report_dir / "engine_latest.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # Human-readable text report
            lines = [
                f"=== DUBBING ENGINE REPORT task={self.task_id} run={self._run_id} ===",
                f"lang={self.lang}  segments={len(results)}  elapsed={elapsed:.2f}s",
                f"adapted={payload['segments_adapted']}  "
                f"skipped={payload['segments_skipped']}  "
                f"strategies={payload['strategies']}",
                "",
                "── PER-SEGMENT LOG ──────────────────────────────────────────────",
            ]
            for r in results:
                status = "✓" if r.passed_validation else "✗"
                lines.append(
                    f"  [{status}] seg#{r.index:02d} strategy={r.recommended_strategy} "
                    f"pred={r.predicted_ms}ms slot={r.slot_ms}ms"
                )
                if r.input_text != r.output_text:
                    lines.append(f"       IN : {r.input_text[:80]}")
                    lines.append(f"       OUT: {r.output_text[:80]}")
                for stage in r.stage_log:
                    if stage.applied:
                        lines.append(f"       [{stage.stage}] {stage.note[:100]}")
                if r.validation_notes:
                    lines.append(f"       NOTES: {'; '.join(r.validation_notes)[:120]}")

            txt_path = report_dir / f"engine_{self._run_id}.txt"
            txt_content = "\n".join(lines) + "\n"
            txt_path.write_text(txt_content, encoding="utf-8")
            (report_dir / "engine_latest.txt").write_text(txt_content, encoding="utf-8")
        except Exception as exc:
            logger.debug("[DubEngine] report write failed: %s", exc)
