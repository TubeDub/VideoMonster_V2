"""Smart segmentation guards (Production TZ §13).

Prevent cutting mid-sentence, between name parts, number+unit, quotes,
or logical clauses. Complements existing merge_segments_for_translation.
"""

from __future__ import annotations

import re
from typing import Any

# Name-like tokens (capitalized sequences) and units after numbers.
_UNIT_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(km|m|cm|mm|kg|g|mg|%|°C|°F|mph|ms|sec|s|min|hrs?|dollars?|usd|€|\$)\b",
    re.I,
)
_NAME_PAIR_RE = re.compile(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b")
_OPEN_QUOTE_RE = re.compile(r"[\"“«]\s*$")
_CLOSE_QUOTE_RE = re.compile(r"^[\"”»]")
_MID_CLAUSE = re.compile(
    r"(?:,\s*|;\s*|\band\b\s*|\bbut\b\s*|\bbecause\b\s*|\bwhile\b\s*)$",
    re.I,
)


def would_break_forbidden(prev: str, nxt: str) -> tuple[bool, str]:
    """Return (True, reason) if joining across the boundary is required."""
    p = str(prev or "").rstrip()
    n = str(nxt or "").lstrip()
    if not p or not n:
        return False, ""

    # Mid-sentence: previous does not end with sentence punctuation.
    if not re.search(r"[.!?…][\"”»)]*\s*$", p):
        # Number + unit split
        join_probe = p + " " + n
        if _UNIT_RE.search(p[-12:] + " " + n[:12]) or (
            re.search(r"\d+\s*$", p) and re.match(r"(?i)(km|m|kg|%|°|mph|ms|sec|min)\b", n)
        ):
            return True, "number_unit"
        # Name + surname across boundary
        tail = re.search(r"([A-Z][a-z]+)\s*$", p)
        head = re.match(r"^([A-Z][a-z]+)\b", n)
        if tail and head:
            return True, "name_surname"
        # Open quote without close
        if _OPEN_QUOTE_RE.search(p) or (p.count('"') % 2 == 1):
            return True, "quote"
        if _MID_CLAUSE.search(p):
            return True, "logical_clause"
        # Generic mid-sentence
        if not re.search(r"[.!?…]\s*$", p):
            return True, "mid_sentence"
    return False, ""


def dynamic_max_chars(model: str = "", *, default: int = 280) -> int:
    """Larger models may handle longer segments (TZ §13)."""
    m = (model or "").lower()
    if any(x in m for x in ("32b", "70b", "gpt-5", "gpt-4.1", "claude", "14b")):
        return 420
    if any(x in m for x in ("7b", "8b", "9b", "gpt-4o")):
        return 320
    if any(x in m for x in ("3b", "1.5b", "2b")):
        return 180
    return default


def enforce_smart_boundaries(
    segments: list[str],
    *,
    model: str = "",
    max_chars: int | None = None,
) -> list[str]:
    """Merge adjacent segments that would otherwise cut forbidden boundaries."""
    if not segments:
        return []
    limit = max_chars if max_chars is not None else dynamic_max_chars(model)
    out: list[str] = []
    buf = str(segments[0] or "").strip()
    for nxt in segments[1:]:
        n = str(nxt or "").strip()
        if not n:
            continue
        must, _reason = would_break_forbidden(buf, n)
        joined = (buf + " " + n).strip()
        if must or (len(buf) < 40 and not re.search(r"[.!?…]\s*$", buf)):
            if len(joined) <= limit * 1.35:
                buf = joined
                continue
        out.append(buf)
        buf = n
    if buf:
        out.append(buf)
    return out


def segmentation_report(segments: list[str]) -> dict[str, Any]:
    violations = 0
    for i in range(len(segments) - 1):
        bad, _ = would_break_forbidden(segments[i], segments[i + 1])
        if bad:
            violations += 1
    return {"segment_count": len(segments), "boundary_risks": violations}
