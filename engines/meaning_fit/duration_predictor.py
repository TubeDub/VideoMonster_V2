"""MF2 — DurationPredictor for UK Meaning Fit (no shorten/expand)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from engines.meaning_fit.flags import meaning_fit_flag
from engines.meaning_fit.types import FitVerdict

Verdict = Literal["TOO_LONG", "TOO_SHORT", "OK"]

# Relative band vs slot: paraphrase may be shorter than full EN slot.
_OK_LOW_RATIO = 0.55
_OK_HIGH_RATIO = 1.0
# Soft overshoot: estimator noise / tiny overflow still counts as OK.
# max(400ms, 5% of slot) — avoids false FIT_FAIL on near-fit UK lines.
_OVER_SLACK_MS = 400
_OVER_SLACK_RATIO = 0.05


@dataclass(frozen=True)
class DurationPrediction:
    text: str
    predicted_ms: int
    slot_ms: int
    verdict: Verdict
    lang: str = "uk"

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "predicted_ms": self.predicted_ms,
            "slot_ms": self.slot_ms,
            "verdict": self.verdict,
            "lang": self.lang,
        }


def predict_ms(text_uk: str, *, lang: str = "uk") -> int:
    """Estimate spoken duration of UK text (no audio synthesis)."""
    from engines.semantic_adaptation import estimate_tts_duration_ms

    return int(estimate_tts_duration_ms(str(text_uk or ""), lang))


def classify_vs_slot(
    predicted_ms: int,
    slot_ms: int,
    *,
    ok_low_ratio: float = _OK_LOW_RATIO,
    ok_high_ratio: float = _OK_HIGH_RATIO,
    over_slack_ms: int | None = None,
) -> Verdict:
    slot = max(0, int(slot_ms or 0))
    pred = max(0, int(predicted_ms or 0))
    if slot <= 0:
        return "OK" if pred == 0 else "TOO_LONG"
    slack = (
        int(over_slack_ms)
        if over_slack_ms is not None
        else max(_OVER_SLACK_MS, int(slot * _OVER_SLACK_RATIO))
    )
    high = int(slot * ok_high_ratio) + slack
    low = int(slot * ok_low_ratio)
    if pred > high:
        return "TOO_LONG"
    if pred < low:
        return "TOO_SHORT"
    return "OK"


def predict_vs_slot(
    text_uk: str,
    slot_ms: int,
    *,
    lang: str = "uk",
) -> DurationPrediction:
    pred = predict_ms(text_uk, lang=lang)
    verdict = classify_vs_slot(pred, slot_ms)
    return DurationPrediction(
        text=str(text_uk or ""),
        predicted_ms=pred,
        slot_ms=int(slot_ms or 0),
        verdict=verdict,
        lang=lang,
    )


def duration_gate(
    text_uk: str,
    slot_ms: int,
    *,
    lang: str = "uk",
    force: bool = False,
) -> dict[str, Any]:
    """Public gate. When Meaning Fit flag OFF → no pipeline effect (informational)."""
    pred = predict_vs_slot(text_uk, slot_ms, lang=lang)
    enabled = bool(force or meaning_fit_flag())
    return {
        "enabled": enabled,
        "affects_pipeline": enabled,
        **pred.as_dict(),
        "verdict": pred.verdict if enabled else "UNKNOWN",
        "raw_verdict": pred.verdict,
    }


def verdict_to_fit(verdict: str) -> FitVerdict:
    v = str(verdict or "").upper()
    if v in ("OK", "TOO_LONG", "TOO_SHORT"):
        return v  # type: ignore[return-value]
    return "UNKNOWN"
