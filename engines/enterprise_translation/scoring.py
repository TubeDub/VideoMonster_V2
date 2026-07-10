"""Intelligent Scoring for translation candidates."""

from __future__ import annotations

import re

from engines.enterprise_translation.contract import PlaceholderContract
from engines.enterprise_translation.registry import PlaceholderRegistry
from engines.enterprise_translation.serializer import EntitySerializer

_LATIN_WORD = re.compile(r"\b[a-zA-Z]{3,}\b")
_GARBAGE = re.compile(r"(\[\[|\{\{|\(\(|\<\<|___|###|PERSON_|ORG_|PLACE_)")


def score_translation(
    source: str,
    candidate: str,
    *,
    registry: PlaceholderRegistry,
    serializer: EntitySerializer,
    engine_id: str,
    expected_tokens: list[str] | None = None,
) -> tuple[float, dict]:
    """
    Score 0-100. Placeholder damage => score 0.
    """
    source = str(source or "")
    candidate = str(candidate or "")
    details: dict = {}

    contract = PlaceholderContract(registry, serializer, engine_id)
    try:
        diag = contract.verify_after_stage(
            candidate,
            stage="scoring",
            expected_tokens=expected_tokens,
            allow_no_tokens=False,
        )
    except Exception as exc:
        details["placeholder"] = {"ok": False, "error": str(exc)}
        return 0.0, details

    damages = diag.get("damages") or []
    missing = diag.get("missing") or []
    if damages or missing or not diag.get("ok"):
        details["placeholder"] = {"ok": False, "damages": damages, "missing": missing}
        return 0.0, details

    details["placeholder"] = {"ok": True}

    # Semantic proxy: length ratio
    src_len = max(len(source.split()), 1)
    cand_len = len(candidate.split())
    completeness = min(1.0, cand_len / src_len) if src_len else 0.5
    if cand_len < src_len * 0.4:
        completeness *= 0.5
    details["completeness"] = round(completeness, 3)

    # English leak
    latin = _LATIN_WORD.findall(candidate)
    latin_ratio = len(latin) / max(cand_len, 1)
    no_english = max(0.0, 1.0 - latin_ratio * 3)
    details["no_english"] = round(no_english, 3)
    details["latin_words"] = latin[:8]

    # Garbage
    garbage_hits = len(_GARBAGE.findall(candidate))
    no_garbage = 1.0 if garbage_hits == 0 else max(0.0, 1.0 - garbage_hits * 0.25)
    details["no_garbage"] = round(no_garbage, 3)

    # Readability / punctuation
    punct_ok = 1.0 if re.search(r"[.!?…]$", candidate.strip()) or len(candidate) < 40 else 0.85
    details["punctuation"] = punct_ok

    # Naturalness proxy: no double spaces, no repeated chars
    natural = 1.0
    if "  " in candidate or re.search(r"(.)\1{4,}", candidate):
        natural = 0.7
    details["naturalness"] = natural

    grammar = 0.9 if not re.search(r"\s[,.]", candidate) else 0.6
    details["grammar"] = grammar

    weights = {
        "completeness": 0.20,
        "no_english": 0.15,
        "no_garbage": 0.15,
        "punctuation": 0.10,
        "naturalness": 0.15,
        "grammar": 0.25,
    }
    semantic = min(1.0, completeness * 0.6 + no_english * 0.4)
    details["semantic"] = round(semantic, 3)

    total = (
        completeness * weights["completeness"]
        + no_english * weights["no_english"]
        + no_garbage * weights["no_garbage"]
        + punct_ok * weights["punctuation"]
        + natural * weights["naturalness"]
        + grammar * weights["grammar"]
    ) * 100.0

    return round(min(100.0, max(0.0, total)), 2), details
