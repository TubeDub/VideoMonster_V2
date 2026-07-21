"""MF7 — Honest Meaning Fit reasons / counters."""

from __future__ import annotations

from typing import Any

from engines.meaning_fit.types import FitResult

_COUNTERS: dict[str, int] = {
    "shorten": 0,
    "expand": 0,
    "fail": 0,
    "already_fits": 0,
    "noop": 0,
}


def reset_counters() -> None:
    for k in _COUNTERS:
        _COUNTERS[k] = 0


def get_counters() -> dict[str, int]:
    return dict(_COUNTERS)


def _bump(key: str) -> None:
    _COUNTERS[key] = int(_COUNTERS.get(key) or 0) + 1


def apply_honest_meaning_fit_reasons(
    seg: dict[str, Any],
    result: FitResult | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stamp truthful text/audio/MF fields. Never claim paraphrase if text unchanged."""
    before = str(
        seg.get("meaning_fit_source")
        or seg.get("translated_text")
        or seg.get("plain_text")
        or ""
    ).strip()
    after = str(
        seg.get("plain_text")
        or seg.get("translated_text")
        or ""
    ).strip()

    status = ""
    reason = ""
    residual = int(seg.get("residual_overflow_ms") or 0)
    if result is not None:
        if isinstance(result, FitResult):
            status = str(result.status or "")
            reason = str(result.reason or "")
            after = str(result.text_uk or after).strip()
            if result.predicted_ms is not None and result.slot_ms:
                residual = max(0, int(result.predicted_ms) - int(result.slot_ms))
            if result.needs_manual:
                seg["needs_manual_review"] = True
        else:
            status = str(result.get("status") or "")
            reason = str(result.get("reason") or "")
            after = str(result.get("text_uk") or after).strip()

    text_changed = bool(before and after and before != after)

    # Forbid false semantic_paraphrase when text did not change
    text_reason = str(seg.get("text_adaptation_reason") or reason or "")
    if not text_changed:
        if "semantic_paraphrase" in text_reason.lower():
            text_reason = "no_text_change"
        # Do not rewrite a real FitResult status — only clear false dump labels
        if status in ("", "success") and "paraphrase" in text_reason.lower():
            text_reason = "no_text_change"
            status = "already_fits" if residual <= 0 else "fit_failed"
            reason = status

    # truncate ≠ success
    method = str(seg.get("meaning_fit_method") or (result.method if isinstance(result, FitResult) else "") or "")
    if method.startswith("truncate") or "truncate" in method:
        status = "rejected_truncate"
        seg["success"] = False
        if str(seg.get("status") or "").upper() == "SUCCESS":
            seg["status"] = "FIT_FAIL"

    if status == "paraphrase_shorten":
        _bump("shorten")
        text_reason = "paraphrase_shorten"
    elif status == "paraphrase_expand":
        _bump("expand")
        text_reason = "paraphrase_expand"
    elif status == "already_fits":
        _bump("already_fits")
        text_reason = text_reason or "already_fits"
    elif status in ("fit_failed", "rejected_truncate"):
        _bump("fail")
        text_reason = status
    elif status == "noop":
        _bump("noop")

    audio_reason = str(seg.get("audio_strategy_reason") or "")
    # Audio trim must not masquerade as paraphrase
    if audio_reason == "trim" and not text_changed:
        if "paraphrase" in text_reason.lower() or "semantic" in text_reason.lower():
            text_reason = "audio_trim_only"

    seg["text_adaptation_reason"] = text_reason
    seg["audio_strategy_reason"] = audio_reason
    seg["meaning_fit_status"] = status or str(seg.get("meaning_fit_status") or "")
    seg["residual_overflow_ms"] = int(residual)
    if text_changed and after:
        seg["plain_text"] = after
        seg["translated_text"] = after

    return {
        "text_adaptation_reason": seg["text_adaptation_reason"],
        "audio_strategy_reason": seg["audio_strategy_reason"],
        "meaning_fit_status": seg["meaning_fit_status"],
        "residual_overflow_ms": seg["residual_overflow_ms"],
        "text_changed": text_changed,
    }
