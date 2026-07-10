"""Smart Segment Optimizer V2 — core optimizer."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.semantic_adaptation import estimate_tts_duration_ms

from engines.smart_segment_optimizer.config import (
    FILL_TARGET_MIN,
    FIT_BAND_MAX,
    FIT_BAND_MIN,
    MAX_LEVEL,
    is_enabled,
)
from engines.smart_segment_optimizer.diff import compute_text_diff
from engines.smart_segment_optimizer.fillers import iter_filler_removals
from engines.smart_segment_optimizer.levels import apply_level, level_name
from engines.smart_segment_optimizer.quality import validate_optimization
from engines.smart_segment_optimizer.timing import (
    allowed_speech_ms,
    segment_duration_ms,
)

logger = logging.getLogger("tubedub.smart_segment_optimizer")


@dataclass
class SegmentOptimizeResult:
    index: int
    original: str
    optimized: str
    changed: bool = False
    skipped: bool = True
    skip_reason: str = "fits_in_slot"
    level_used: int = 0
    stop_reason: str = ""
    segment_ms: int = 0
    slot_ms: int = 0
    est_ms_before: int = 0
    est_ms_after: int = 0
    ms_saved: int = 0
    fill_percent_before: float = 0.0
    fill_percent_after: float = 0.0
    underfill: bool = False
    overflow: bool = False
    quality: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    text_for_tts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "original": self.original,
            "optimized": self.optimized,
            "text_for_tts": self.text_for_tts or self.optimized,
            "changed": self.changed,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "level_used": self.level_used,
            "stop_reason": self.stop_reason,
            "segment_ms": self.segment_ms,
            "slot_ms": self.slot_ms,
            "est_ms_before": self.est_ms_before,
            "est_ms_after": self.est_ms_after,
            "ms_saved": self.ms_saved,
            "fill_percent_before": self.fill_percent_before,
            "fill_percent_after": self.fill_percent_after,
            "underfill": self.underfill,
            "overflow": self.overflow,
            "quality": self.quality,
            "diff": self.diff,
            "steps": self.steps,
        }


def slot_ms_from_timing(timing: Any, *, margin_ms: int | None = None) -> int:
    """Allowed speech ms (backward compatible export)."""
    from engines.smart_segment_optimizer.config import SLOT_MARGIN_MS

    m = SLOT_MARGIN_MS if margin_ms is None else margin_ms
    return allowed_speech_ms(timing, margin_ms=m)


def _fill_percent(est_ms: int, segment_ms: int) -> float:
    if segment_ms <= 0:
        return 0.0
    return round(100.0 * est_ms / segment_ms, 1)


def _in_fit_band(est_ms: int, segment_ms: int, allowed_ms: int) -> bool:
    if segment_ms <= 0:
        return True
    ratio = est_ms / segment_ms
    return FIT_BAND_MIN <= ratio <= FIT_BAND_MAX and est_ms <= allowed_ms


def _fits_allowed(est_ms: int, allowed_ms: int) -> bool:
    return est_ms <= allowed_ms


def optimize_segment(
    text: str,
    slot_ms: int,
    *,
    index: int = 0,
    source_hint: str = "",
    tgt_lang: str = "ru",
    app_dir: Path | None = None,
    segment_ms: int | None = None,
) -> SegmentOptimizeResult:
    """
    Shorten only on real overflow. Target: fullest text that fits (95–100% fill ideal).
    Text unchanged when already in fit band — zero punctuation/word changes.
    """
    original = " ".join(str(text or "").split())
    seg_ms = int(segment_ms or slot_ms)
    allowed_ms = int(slot_ms)
    est_before = estimate_tts_duration_ms(original, tgt_lang)

    result = SegmentOptimizeResult(
        index=index,
        original=original,
        optimized=original,
        text_for_tts=original,
        segment_ms=seg_ms,
        slot_ms=allowed_ms,
        est_ms_before=est_before,
        est_ms_after=est_before,
        fill_percent_before=_fill_percent(est_before, seg_ms),
        fill_percent_after=_fill_percent(est_before, seg_ms),
    )

    if not original or allowed_ms <= 0:
        result.skip_reason = "empty_or_invalid_slot"
        return result

    result.underfill = est_before < int(seg_ms * FILL_TARGET_MIN)
    result.overflow = est_before > allowed_ms

    if _in_fit_band(est_before, seg_ms, allowed_ms):
        result.skip_reason = "fits_in_slot"
        return result

    if not result.overflow:
        result.skip_reason = "underfill_only"
        return result

    result.skipped = False
    fitting: list[tuple[str, int, int, list]] = []

    def _try_candidate(candidate: str, level: int, reason: str, extra: dict | None = None):
        nonlocal fitting
        if candidate == original and level == 0:
            return
        est = estimate_tts_duration_ms(candidate, tgt_lang)
        q = validate_optimization(
            original,
            candidate,
            source_hint=source_hint,
            tgt_lang=tgt_lang,
            app_dir=app_dir,
            allowed_ms=allowed_ms,
            segment_ms=seg_ms,
        )
        step = {
            "level": level,
            "name": level_name(level) if level else "check",
            "applied": candidate != original,
            "reason": reason,
            "est_ms": est,
            "fill_percent": _fill_percent(est, seg_ms),
            "quality_ok": q.ok,
            "quality_issues": q.issues,
        }
        if extra:
            step.update(extra)
        result.steps.append(step)
        if not q.ok:
            return
        if _fits_allowed(est, allowed_ms):
            fitting.append((candidate, est, level, step))

    current = original

    for step in iter_filler_removals(current, tgt_lang):
        _try_candidate(step.text, 1, step.reason, {"removed": step.removed})
        current = step.text

    if not fitting:
        for level in range(2, MAX_LEVEL + 1):
            candidate, reason = apply_level(current, level)
            if candidate == current:
                result.steps.append(
                    {
                        "level": level,
                        "name": level_name(level),
                        "applied": False,
                        "reason": reason,
                    }
                )
                continue
            _try_candidate(candidate, level, reason)
            if fitting:
                break
            current = candidate

    if not fitting:
        result.skipped = True
        result.skip_reason = "no_safe_optimization"
        result.stop_reason = "quality_rejected_or_still_overflow"
        return result

    best_text, best_est, level_used, _ = max(
        fitting, key=lambda x: (x[1], len(x[0].split()))
    )

    result.optimized = best_text
    result.text_for_tts = best_text
    result.changed = best_text != original
    result.level_used = level_used
    result.est_ms_after = best_est
    result.ms_saved = max(0, est_before - best_est)
    result.fill_percent_after = _fill_percent(best_est, seg_ms)
    result.stop_reason = "fits_with_max_fill"
    result.skip_reason = "optimized_to_fit"
    result.quality = validate_optimization(
        original,
        best_text,
        source_hint=source_hint,
        tgt_lang=tgt_lang,
        app_dir=app_dir,
        allowed_ms=allowed_ms,
        segment_ms=seg_ms,
    ).to_dict()
    result.diff = compute_text_diff(original, best_text)

    logger.info(
        "SSO idx=%d L%d est %d→%d ms seg=%d fill=%.0f%% saved=%d",
        index,
        level_used,
        est_before,
        best_est,
        seg_ms,
        result.fill_percent_after,
        result.ms_saved,
    )
    return result


def optimize_segments(
    segments: list[str],
    timing_map: list[Any],
    *,
    source_segments: list[str] | None = None,
    tgt_lang: str = "ru",
    src_lang: str = "en",
    app_dir: Path | None = None,
    task_id: str = "",
) -> tuple[list[str], list[SegmentOptimizeResult], dict[str, Any]]:
    app_dir = app_dir or Path(__file__).resolve().parent.parent.parent
    src = source_segments or []
    t0 = time.perf_counter()
    out: list[str] = []
    reports: list[SegmentOptimizeResult] = []

    for i, text in enumerate(segments):
        timing = timing_map[i] if i < len(timing_map) else None
        seg_ms = segment_duration_ms(timing) if timing is not None else 5000
        allowed = allowed_speech_ms(timing) if timing is not None else seg_ms - 40
        hint = src[i] if i < len(src) else ""
        rep = optimize_segment(
            text,
            allowed,
            index=i,
            source_hint=hint,
            tgt_lang=tgt_lang,
            app_dir=app_dir,
            segment_ms=seg_ms,
        )
        reports.append(rep)
        out.append(rep.optimized if rep.changed else str(text or ""))

    changed = sum(1 for r in reports if r.changed)
    skipped = sum(1 for r in reports if r.skipped)
    underfill = sum(1 for r in reports if r.underfill)
    meta = {
        "enabled": True,
        "task_id": task_id,
        "segments": len(segments),
        "changed": changed,
        "skipped": skipped,
        "underfill": underfill,
        "elapsed_sec": round(time.perf_counter() - t0, 3),
        "tgt_lang": tgt_lang,
        "src_lang": src_lang,
    }

    from engines.smart_segment_optimizer.dev_report import save_dev_report

    report_path = save_dev_report(app_dir, reports, meta, task_id=task_id)
    meta["dev_report_path"] = report_path

    logger.info(
        "[SSO] %d segments, %d changed, %d skipped, %d underfill, %.2fs",
        len(segments),
        changed,
        skipped,
        underfill,
        meta["elapsed_sec"],
    )
    return out, reports, meta
