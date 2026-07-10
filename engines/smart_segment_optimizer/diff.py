"""Word-level diff for Smart Segment Optimizer dev reports."""

from __future__ import annotations

import re
from typing import Any


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w']+", str(text or ""), flags=re.UNICODE)


def compute_text_diff(original: str, optimized: str) -> dict[str, Any]:
    orig_words = _tokens(original)
    opt_words = _tokens(optimized)
    orig_set = set(w.lower() for w in orig_words)
    opt_set = set(w.lower() for w in opt_words)

    removed = [w for w in orig_words if w.lower() not in opt_set]
    added = [w for w in opt_words if w.lower() not in orig_set]

    replaced: list[dict[str, str]] = []
    n = min(len(orig_words), len(opt_words))
    for i in range(n):
        if orig_words[i].lower() != opt_words[i].lower():
            replaced.append({"from": orig_words[i], "to": opt_words[i], "position": i})

    reordered = (
        sorted(w.lower() for w in orig_words) == sorted(w.lower() for w in opt_words)
        and orig_words != opt_words
        and not removed
        and not added
    )

    return {
        "removed_words": removed,
        "added_words": added,
        "replaced_words": replaced,
        "reordered": reordered,
    }
