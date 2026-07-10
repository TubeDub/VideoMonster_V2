"""Conversational natural speech — avoid literal calques."""

from __future__ import annotations

import re

from engines.ai_core.semantic_agent.rule_engine import (
    apply_synonym_replacement,
    dedupe_consecutive_subjects,
    fix_literal_phrasing,
)
from engines.mt.lang_codes import normalize_lang


def to_conversational(
    text: str,
    *,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
) -> str:
    """Pass 4 — conversational form without changing facts."""
    raw = str(text or "").strip()
    if not raw:
        return raw

    lang = normalize_lang(tgt_lang)
    out = dedupe_consecutive_subjects(raw, prev_context, lang)
    out = fix_literal_phrasing(out, lang)
    out = apply_synonym_replacement(out, lang)

    # Remove stiff formal openings common in MT
    formal_openers = [
        (r"^Следует отметить[, ]+", ""),
        (r"^Необходимо отметить[, ]+", ""),
        (r"^Важно отметить[, ]+", ""),
        (r"^Слід зазначити[, ]+", ""),
    ]
    for pat, repl in formal_openers:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)

    return out.strip() or raw
