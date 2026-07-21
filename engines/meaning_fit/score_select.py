"""MF5 — Score + Select best Meaning Fit variant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.meaning_fit.duration_predictor import predict_vs_slot
from engines.meaning_fit.exceptions import TruncateNotMeaningFitError
from engines.meaning_fit.flags import meaning_fit_flag
from engines.meaning_fit.semantic_shorten import _is_chop, _preserve_entities
from engines.meaning_fit.skeleton import reject_truncate_as_success
from engines.meaning_fit.types import FitResult


@dataclass
class ScoredVariant:
    text: str
    method: str
    score: float
    predicted_ms: int
    verdict: str
    meaning: float
    duration_fit: float
    names: float
    truncate_penalty: float
    meta: dict[str, Any] = field(default_factory=dict)


def _duration_fit_score(predicted_ms: int, slot_ms: int) -> float:
    slot = max(1, int(slot_ms or 0))
    pred = max(0, int(predicted_ms or 0))
    ratio = pred / slot
    if 0.55 <= ratio <= 1.0:
        # closer to 0.85 is ideal spoken fill
        return 1.0 - abs(ratio - 0.85) * 0.5
    if ratio > 1.0:
        return max(0.0, 1.0 - (ratio - 1.0) * 2.0)
    return max(0.0, ratio / 0.55)


def _meaning_score(source: str, candidate: str) -> float:
    if not candidate:
        return 0.0
    if _is_chop(source, candidate):
        return 0.0
    sw = set(w.lower() for w in source.split() if len(w) > 2)
    cw = set(w.lower() for w in candidate.split() if len(w) > 2)
    if not sw:
        return 0.8 if candidate else 0.0
    overlap = len(sw & cw) / max(1, len(sw))
    # Paraphrase may replace words — reward non-prefix rewrite with some overlap or goat map
    if candidate != source and not source.startswith(candidate):
        return max(0.55, min(1.0, 0.45 + overlap))
    return overlap


def _names_score(source: str, candidate: str) -> float:
    return 1.0 if _preserve_entities(source, candidate) else 0.0


def score_variant(
    source: str,
    candidate: str,
    slot_ms: int,
    *,
    method: str = "",
) -> ScoredVariant:
    method_l = str(method or "").lower()
    if method_l.startswith("truncate") or method_l in ("chop", "slice_chars", "tail_cut"):
        reject_truncate_as_success(method_l, text_uk=candidate)
    pred = predict_vs_slot(candidate, slot_ms)
    trunc_pen = 1.0 if _is_chop(source, candidate) else 0.0
    dur = _duration_fit_score(pred.predicted_ms, slot_ms)
    meaning = _meaning_score(source, candidate)
    names = _names_score(source, candidate)
    # Heavy penalty for truncate
    score = (0.45 * dur) + (0.40 * meaning) + (0.15 * names) - (0.90 * trunc_pen)
    return ScoredVariant(
        text=candidate,
        method=method,
        score=score,
        predicted_ms=pred.predicted_ms,
        verdict=pred.verdict,
        meaning=meaning,
        duration_fit=dur,
        names=names,
        truncate_penalty=trunc_pen,
    )


def select_best(
    source: str,
    variants: list[dict[str, Any]] | list[ScoredVariant],
    slot_ms: int,
    *,
    min_score: float = 0.45,
) -> FitResult:
    """Pick best variant; else FIT_FAIL / needs_manual (never silent original as success)."""
    if not meaning_fit_flag():
        return FitResult(
            text_uk=source,
            status="noop",
            reason="flag_off_legacy",
            slot_ms=slot_ms,
            success=False,
            method="noop",
            meta={"enabled": False},
        )

    scored: list[ScoredVariant] = []
    for v in variants or []:
        if isinstance(v, ScoredVariant):
            scored.append(v)
            continue
        text = str(v.get("text") or v.get("text_uk") or "")
        method = str(v.get("method") or "")
        try:
            scored.append(score_variant(source, text, slot_ms, method=method))
        except TruncateNotMeaningFitError as exc:
            scored.append(
                ScoredVariant(
                    text=text,
                    method=method,
                    score=-1.0,
                    predicted_ms=0,
                    verdict="TOO_LONG",
                    meaning=0.0,
                    duration_fit=0.0,
                    names=0.0,
                    truncate_penalty=1.0,
                    meta={"rejected": str(exc)},
                )
            )

    # Also score original as already_fits candidate
    scored.append(score_variant(source, source, slot_ms, method="original"))

    scored.sort(key=lambda s: s.score, reverse=True)
    best = scored[0] if scored else None
    if best is None or best.score < min_score or best.truncate_penalty >= 0.5:
        pred0 = predict_vs_slot(source, slot_ms)
        return FitResult(
            text_uk=source,
            status="fit_failed",
            reason="fit_failed",
            predicted_ms=pred0.predicted_ms,
            slot_ms=slot_ms,
            verdict=pred0.verdict,
            success=False,
            needs_manual=True,
            method="select_best",
            meta={
                "candidates": [s.__dict__ for s in scored[:5]],
                "note": "not_silent_original_success",
            },
        )

    if best.text == source and best.verdict == "OK":
        reason = "already_fits"
        status = "already_fits"
    elif best.method in ("semantic_shorten", "paraphrase_shorten") or (
        best.text != source and best.predicted_ms <= predict_vs_slot(source, slot_ms).predicted_ms
    ):
        reason = "paraphrase_shorten"
        status = "paraphrase_shorten"
    elif best.method in ("semantic_expand", "paraphrase_expand"):
        reason = "paraphrase_expand"
        status = "paraphrase_expand"
    elif best.text != source:
        # Infer direction
        if best.predicted_ms < predict_vs_slot(source, slot_ms).predicted_ms:
            reason = "paraphrase_shorten"
            status = "paraphrase_shorten"
        else:
            reason = "paraphrase_expand"
            status = "paraphrase_expand"
    else:
        reason = "fit_failed"
        status = "fit_failed"

    # Soft near-fit: high meaning score + tiny overshoot → already_fits
    soft_ok = False
    if best.verdict == "TOO_LONG" and best.truncate_penalty < 0.5 and best.score >= 0.7:
        overflow = max(0, int(best.predicted_ms) - int(slot_ms or 0))
        soft_cap = max(400, int((slot_ms or 0) * 0.08))
        if overflow <= soft_cap:
            soft_ok = True
            best = ScoredVariant(
                text=best.text,
                method=best.method,
                score=best.score,
                predicted_ms=best.predicted_ms,
                verdict="OK",
                meaning=best.meaning,
                duration_fit=best.duration_fit,
                names=best.names,
                truncate_penalty=best.truncate_penalty,
                meta={**best.meta, "soft_near_fit": True, "overflow_ms": overflow},
            )
            if best.text == source:
                reason = "already_fits"
                status = "already_fits"

    success = (best.verdict == "OK" or soft_ok) and best.truncate_penalty < 0.5
    return FitResult(
        text_uk=best.text if success or best.text != source else source,
        status=status if success else "fit_failed",
        reason=reason if success else "fit_failed",
        predicted_ms=best.predicted_ms,
        slot_ms=slot_ms,
        verdict="OK" if success else best.verdict,  # type: ignore[arg-type]
        success=success,
        needs_manual=not success,
        method=best.method or "select_best",
        meta={"score": best.score, "all": [s.__dict__ for s in scored[:5]]},
    )
