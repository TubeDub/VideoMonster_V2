"""Hallucination / garbage detection rules."""

from __future__ import annotations

import re
from collections import Counter

from engines.tqe.rules._registry import register


@register("hallucination")
def check_hallucination(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    text = translation or ""
    for pat in (
        re.compile(r"(.)\1{5,}"),
        re.compile(r"\b(asdf|lorem|test123|xxx+|null|undefined)\b", re.I),
        re.compile(r"<speak\b", re.I),
        re.compile(r"\{[a-z_]+\}", re.I),
    ):
        if pat.search(text):
            errors.append(
                {
                    "code": "garbage_or_ssml",
                    "detail": pat.pattern[:60],
                    "severity": "critical",
                }
            )
    words = re.findall(r"\w+", text.lower())
    if words:
        common, cnt = Counter(words).most_common(1)[0]
        if cnt >= 6 and len(common) > 2:
            errors.append(
                {"code": "word_loop", "token": common, "severity": "critical"}
            )
    return errors
