"""Validate fixes — meaning must not change drastically."""

from __future__ import annotations

import re


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\b[\w'-]+\b", str(text or ""), flags=re.UNICODE)}


def is_nonsense(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t) < 2:
        return True
    if re.search(r"(.)\1{8,}", t):
        return True
    words = re.findall(r"\w+", t.lower())
    if len(words) >= 4 and len(set(words)) == 1:
        return True
    return False


def accept_fix(
    before: str,
    after: str,
    *,
    original: str = "",
    min_ratio: float = 0.45,
    max_ratio: float = 2.2,
) -> bool:
    """True if after is safe to use."""
    b = str(before or "").strip()
    a = str(after or "").strip()
    if not a:
        return False
    if a == b:
        return True
    if is_nonsense(a) and not is_nonsense(b):
        return False

    bw = max(len(re.findall(r"\w+", b)), 1)
    aw = len(re.findall(r"\w+", a))
    ratio = aw / bw
    if ratio < min_ratio or ratio > max_ratio:
        return False

    if original:
        ow = max(len(re.findall(r"\w+", original)), 1)
        if aw > ow * 2.5 or aw < ow * 0.25:
            return False

    return True


def meaning_overlap_ok(before: str, after: str, *, threshold: float = 0.35) -> bool:
    """Rough lexical overlap — not semantic MT, just safety guard."""
    wb = _words(before)
    wa = _words(after)
    if not wb or not wa:
        return True
    overlap = len(wb & wa) / max(len(wb), len(wa))
    return overlap >= threshold
