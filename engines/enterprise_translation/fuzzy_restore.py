"""Fuzzy placeholder restore — Levenshtein + difflib."""

from __future__ import annotations

import difflib
import re

from engines.enterprise_translation.registry import PlaceholderRegistry
from engines.enterprise_translation.serializer import EntitySerializer

_DAMAGE_RE = re.compile(
    r"(?:\[\[|\{|\(|\<|__|#)?"
    r"(PERSON|ORG|PLACE|TITLE|PRODUCT|COMPANY|EVENT|DATE)[_\s]?(\d+)"
    r"(?:\]\]|\}|\)|\>|__|#)?",
    re.IGNORECASE,
)


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]


def _normalize_token(raw: str) -> str:
    s = re.sub(r"[^\w]", "_", str(raw or "").upper())
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def fuzzy_restore_tokens(
    damaged: str,
    registry: PlaceholderRegistry,
    *,
    engine_id: str = "default",
    serializer: EntitySerializer | None = None,
    threshold: float = 0.72,
) -> tuple[str | None, str | None]:
    """
    Try to map damaged token to entity_id.
    Returns (entity_id, canonical_token) or (None, None).
    """
    serializer = serializer or EntitySerializer()
    norm_damaged = _normalize_token(damaged)
    if not norm_damaged:
        return None, None

    best_id: str | None = None
    best_score = 0.0

    for rec in registry.all_records():
        candidates = [rec.entity_id] + list(rec.restore_variants or []) + [rec.original]
        for cand in candidates:
            norm_c = _normalize_token(cand)
            if not norm_c:
                continue
            ratio = difflib.SequenceMatcher(None, norm_damaged, norm_c).ratio()
            max_len = max(len(norm_damaged), len(norm_c), 1)
            lev = _levenshtein(norm_damaged, norm_c)
            lev_score = 1.0 - lev / max_len
            score = max(ratio, lev_score)
            if score > best_score:
                best_score = score
                best_id = rec.entity_id

    if best_id and best_score >= threshold:
        token = serializer.get_token_for_engine(best_id, engine_id)
        return best_id, token
    return None, None


def scan_and_fuzzy_restore(
    text: str,
    registry: PlaceholderRegistry,
    engine_id: str = "default",
) -> tuple[str, list[str]]:
    """Find damaged patterns in text and attempt restore to entity display."""
    working = text
    notes: list[str] = []
    for m in _DAMAGE_RE.finditer(text):
        damaged = m.group(0)
        eid, _ = fuzzy_restore_tokens(damaged, registry, engine_id=engine_id)
        if eid:
            rec = registry.get(eid)
            if rec:
                working = working.replace(damaged, rec.display or rec.original, 1)
                notes.append(f"{damaged}->{eid}")
    return working, notes
