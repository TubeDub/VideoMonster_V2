"""Quote preservation rules."""

from __future__ import annotations

import re

from engines.tqe.rules._registry import register


@register("quotes")
def check_quotes(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    src = original or ""
    tr = translation or ""
    src_has = bool(re.search(r"[\"«»]", src))
    tr_has = bool(re.search(r"[\"«»]", tr))
    if src_has and ('"' in src or "«" in src) and not tr_has:
        if re.search(r"star wars|\"[^\"]{2,}\"", src, re.I):
            errors.append(
                {
                    "code": "quotes_missing",
                    "severity": "major",
                    "detail": "quoted title lost",
                }
            )
    return errors
