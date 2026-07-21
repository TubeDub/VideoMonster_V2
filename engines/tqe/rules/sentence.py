"""Sentence completeness rules."""

from __future__ import annotations

import re

from engines.tqe.rules._registry import register


@register("sentence")
def check_sentence(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    text = (translation or "").strip()
    if not text:
        return [{"code": "empty", "severity": "critical"}]

    src = (original or "").strip()
    src_complete = bool(src) and src[-1] in ".!?…"
    if src_complete and text[-1] not in ".!?…»\"":
        errors.append(
            {
                "code": "incomplete_sentence",
                "severity": "major",
                "detail": "source ends with terminal punctuation, translation does not",
            }
        )

    if re.match(r"^(і|а|але|що|як|коли)\s", text, re.I) and len(text.split()) < 6:
        errors.append({"code": "orphan_connector", "severity": "major"})
    return errors
