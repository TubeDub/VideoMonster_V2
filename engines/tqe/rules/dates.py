"""Date preservation rules."""

from __future__ import annotations

import re

from engines.tqe.rules._registry import register

_DATE_PAT = re.compile(
    r"\b(?:\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{4}|years?\s+later|weeks?\s+later)\b",
    re.I,
)


@register("dates")
def check_dates(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    src_dates = _DATE_PAT.findall(original or "")
    tr = translation or ""
    for d in src_dates:
        low = d.lower()
        if "year" in low:
            if not re.search(r"рік|рок|год", tr, re.I):
                errors.append({"code": "date_missing", "token": d, "severity": "major"})
        elif "week" in low:
            if not re.search(r"тижн", tr, re.I):
                errors.append({"code": "date_missing", "token": d, "severity": "major"})
        elif d.isdigit() and len(d) == 4:
            if d not in tr:
                errors.append({"code": "year_missing", "token": d, "severity": "major"})
    return errors
