"""MF1 — Meaning Fit skeleton stubs (no-op when flags OFF).

Full path: duration (MF2) + shorten/expand (MF3/4) + select (MF5) via orchestrator.
"""

from __future__ import annotations

from typing import Any

from engines.meaning_fit.exceptions import TruncateNotMeaningFitError
from engines.meaning_fit.flags import meaning_fit_flag
from engines.meaning_fit.types import FitRequest, FitResult, MeaningText

_TRUNCATE_METHODS = frozenset(
    {
        "truncate_to_n_chars",
        "truncate",
        "chop",
        "slice_chars",
        "tail_cut",
    }
)


def reject_truncate_as_success(
    method: str,
    *,
    text_uk: str = "",
    stage: str = "mf1",
) -> None:
    """Hard rule: truncate/chop is never Meaning Fit success."""
    m = str(method or "").strip().lower()
    if m in _TRUNCATE_METHODS or m.startswith("truncate"):
        raise TruncateNotMeaningFitError(
            "truncate_to_n_chars must not be Meaning Fit success",
            details={"method": method, "stage": stage, "text_preview": text_uk[:80]},
        )


def fit_meaning(request: FitRequest | dict[str, Any]) -> FitResult:
    """Entry: delegates to orchestrator when enabled; else legacy no-op."""
    if isinstance(request, dict):
        method = str((request.get("meta") or {}).get("method") or "")
        if method:
            reject_truncate_as_success(method, text_uk=str(request.get("text_uk") or ""))
    elif isinstance(request, FitRequest) and request.meta.get("method"):
        reject_truncate_as_success(
            str(request.meta.get("method")),
            text_uk=request.text_uk,
        )

    if not meaning_fit_flag():
        text = (
            request.text_uk
            if isinstance(request, FitRequest)
            else str(request.get("text_uk") or "")
        )
        slot = (
            request.slot_ms
            if isinstance(request, FitRequest)
            else int(request.get("slot_ms") or 0)
        )
        return FitResult(
            text_uk=text,
            status="noop",
            reason="flag_off_legacy",
            slot_ms=slot,
            success=False,
            method="noop",
            meta={"enabled": False, "noop": True, "skeleton": True},
        )

    from engines.meaning_fit.orchestrator import fit_segment

    return fit_segment(request)


def skeleton_meaning_fit(
    text_uk: str,
    slot_ms: int,
    *,
    method: str = "",
) -> dict[str, Any]:
    req = FitRequest(text_uk=text_uk, slot_ms=slot_ms, meta={"method": method})
    return fit_meaning(req).as_dict()


def wrap_meaning_text(text: str, *, lang: str = "uk") -> MeaningText:
    return MeaningText(text=text, lang=lang)
