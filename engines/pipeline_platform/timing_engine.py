"""Timing Engine — Stage 5. Uses Word Timing Map; start immutable, end adaptive."""

from __future__ import annotations

from typing import Any

from engines.pipeline_platform.translation_optimizer_platform import optimize_translation_text


def _estimate_ms(text: str, lang: str) -> int:
    try:
        from engines.semantic_adaptation import estimate_tts_duration_ms

        return int(estimate_tts_duration_ms(text, lang=lang or "uk"))
    except Exception:
        return max(80, len((text or "").split()) * 180)


def run_timing_engine(
    *,
    text: str,
    slot_ms: int,
    word_timing_map: dict[str, Any],
    src_lang: str = "en",
    tgt_lang: str = "uk",
    allow_stretch: bool = True,
    max_stretch: float = 1.18,
) -> dict[str, Any]:
    """
    TZ cascade: Optimizer → Time Stretch → Warning → Error.
    Segment start_ms is never changed; end may adapt.
    """
    original = (text or "").strip()
    wtm = dict(word_timing_map or {})
    words = list(wtm.get("words") or [])
    start_ms = int(wtm.get("segment_start_ms") or 0)
    if words and not start_ms:
        start_ms = int(words[0].get("start_ms", 0))
    end_ms = int(wtm.get("segment_end_ms") or slot_ms or 0)
    if words and end_ms <= start_ms:
        end_ms = int(words[-1].get("end_ms", start_ms + slot_ms))

    budget = max(slot_ms, end_ms - start_ms) if slot_ms else max(end_ms - start_ms, 0)
    est = _estimate_ms(original, tgt_lang)
    rules: list[str] = ["start_immutable"]
    warnings: list[str] = []
    errors: list[str] = []
    text_out = original
    stretch_ratio = 1.0
    quality_score = 1.0

    if est <= int(budget * 0.92):
        return {
            "text_out": text_out,
            "duration_ms": est,
            "budget_ms": budget,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "stretch_ratio": stretch_ratio,
            "word_timing_map": wtm,
            "warnings": warnings,
            "errors": errors,
            "rules_applied": rules,
            "quality_score": quality_score,
        }

    rules.append("translation_optimizer")
    opt = optimize_translation_text(original, slot_ms=budget, src_lang=src_lang, tgt_lang=tgt_lang)
    if opt.changed:
        text_out = opt.optimized
        est = _estimate_ms(text_out, tgt_lang)
        quality_score = float(opt.quality_after.get("score", 0.8) or 0.8)

    if est > int(budget * 0.92) and allow_stretch:
        rules.append("time_stretch")
        stretch_ratio = min(max_stretch, max(1.0, est / max(budget, 1)))
        est = int(est / stretch_ratio)

    if est > budget:
        rules.append("timing_warning")
        warnings.append("timing_warning")
        quality_score = min(quality_score, 0.6)
        adapted_end = start_ms + est
        wtm = dict(wtm)
        wtm["segment_end_ms"] = adapted_end
        end_ms = adapted_end

    if est > int(budget * 1.15):
        rules.append("timing_error")
        errors.append("timing_error")
        quality_score = min(quality_score, 0.3)

    return {
        "text_out": text_out,
        "duration_ms": est,
        "budget_ms": budget,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "stretch_ratio": round(stretch_ratio, 4),
        "word_timing_map": wtm,
        "warnings": warnings,
        "errors": errors,
        "rules_applied": rules,
        "quality_score": quality_score,
    }
