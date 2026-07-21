"""Number preservation rules."""

from __future__ import annotations

import re

from engines.tqe.rules._registry import register


@register("numbers")
def check_numbers(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    src_nums = re.findall(r"\b\d+\b", original or "")
    tr = translation or ""
    for n in src_nums:
        if n not in tr:
            errors.append({"code": "number_missing", "token": n, "severity": "major"})
    return errors
