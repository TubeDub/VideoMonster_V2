"""Timing fitness rules."""

from __future__ import annotations

from engines.tqe.rules._registry import register


@register("timing")
def check_timing(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    slot_ms = int(ctx.get("slot_ms") or 0)
    text = (translation or "").strip()
    if slot_ms <= 0 or not text:
        return errors

    est_ms = int(len(text) / 12.0 * 1000)
    ratio = est_ms / max(slot_ms, 1)
    if ratio > 1.45:
        errors.append(
            {
                "code": "too_long_for_slot",
                "severity": "major",
                "detail": f"est_ms={est_ms} slot_ms={slot_ms} ratio={ratio:.2f}",
            }
        )
    if ratio < 0.35 and len(original.split()) >= 12:
        errors.append(
            {
                "code": "over_compressed",
                "severity": "major",
                "detail": f"est_ms={est_ms} slot_ms={slot_ms} ratio={ratio:.2f}",
            }
        )
    return errors
