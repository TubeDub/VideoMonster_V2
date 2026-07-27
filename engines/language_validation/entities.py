# -*- coding: utf-8 -*-
"""Mask international entities so Language Validation ignores brands/names/abbr."""

from __future__ import annotations

import re
from typing import Iterable

# Explicit allow-list from TZ + common media / tech entities.
_KNOWN_ENTITIES: tuple[str, ...] = (
    "George Lucas",
    "George Jr",
    "George Jr.",
    "Haskell Wexler",
    "Star Wars",
    "Hollywood",
    "YouTube",
    "OpenAI",
    "GitHub",
    "Google",
    "Apple",
    "iPhone",
    "Android",
    "Facebook",
    "Instagram",
    "TikTok",
    "Twitter",
    "Netflix",
    "Disney",
    "Pixar",
    "USC",
    "Fiat",
    "BMW",
    "Tesla",
    "NASA",
    "FBI",
    "CIA",
    "BBC",
    "CNN",
    "NBA",
    "NHL",
    "USB",
    "GPS",
    "PDF",
    "MP3",
    "MP4",
    "AI",
    "UI",
    "UX",
    "API",
    "SDK",
    "CPU",
    "GPU",
    "LED",
    "LCD",
    "HDMI",
    "Wi-Fi",
    "WiFi",
    "OK",
    "Jr",
    "Jr.",
    "Sr",
    "Sr.",
)

# Multi-word first (longer match wins).
_ENTITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\b{re.escape(e)}\b", re.I) for e in sorted(_KNOWN_ENTITIES, key=len, reverse=True)
]

# Title-Case multi-word Latin names: «Haskell Wexler», «George Lucas»
_TITLE_NAME = re.compile(
    r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b"
)
# ALLCAPS / mixed acronyms 2–6 letters: USC, BMW, AI
_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")
# Latin tokens with digits / model-ish: iPhone, Fiat500
_LATIN_PRODUCT = re.compile(r"\b[A-Za-z][A-Za-z0-9\-]{1,24}\b")


def mask_entities(
    text: str,
    *,
    extra: Iterable[str] | None = None,
) -> tuple[str, list[str]]:
    """Replace brands / names / acronyms with ``⟨E⟩`` placeholders.

    Returns ``(masked_text, replaced_tokens)``. Full-text analysis still runs on
    the original for length checks; language scoring uses the masked form.
    """
    out = str(text or "")
    replaced: list[str] = []
    if not out.strip():
        return out, replaced

    extras = [e.strip() for e in (extra or []) if e and len(e.strip()) >= 2]
    for ent in sorted(extras, key=len, reverse=True):
        pat = re.compile(rf"\b{re.escape(ent)}\b", re.I)
        if pat.search(out):
            replaced.append(ent)
            out = pat.sub("⟨E⟩", out)

    for pat in _ENTITY_PATTERNS:
        def _sub(m: re.Match[str], _p=pat) -> str:
            tok = m.group(0)
            replaced.append(tok)
            return "⟨E⟩"

        out, n = pat.subn(_sub, out)
        if n:
            pass

    def _keep_if_cyrillic_context(match: re.Match[str]) -> str:
        tok = match.group(0)
        # Do not mask short function words that look Title Case by accident
        if tok.lower() in {
            "the",
            "and",
            "but",
            "for",
            "from",
            "with",
            "that",
            "this",
            "was",
            "were",
            "have",
            "been",
        }:
            return tok
        replaced.append(tok)
        return "⟨E⟩"

    out = _TITLE_NAME.sub(_keep_if_cyrillic_context, out)
    out = _ACRONYM.sub(lambda m: (replaced.append(m.group(0)) or "⟨E⟩"), out)

    # Remaining Latin islands inside mostly-Cyrillic lines → treat as entities
    cyr = len(re.findall(r"[А-Яа-яЁёІіЇїЄєҐґ]", out))
    if cyr >= 8:
        def _latin_island(m: re.Match[str]) -> str:
            tok = m.group(0)
            if tok in ("⟨E⟩",) or tok.startswith("⟨"):
                return tok
            low = tok.lower()
            # English function words still matter for leak detection — keep them
            if low in {
                "that",
                "was",
                "from",
                "the",
                "and",
                "but",
                "had",
                "have",
                "been",
                "with",
                "for",
                "this",
                "are",
                "is",
                "of",
                "to",
                "in",
                "on",
                "at",
            }:
                return tok
            replaced.append(tok)
            return "⟨E⟩"

        out = _LATIN_PRODUCT.sub(_latin_island, out)

    out = re.sub(r"(?:⟨E⟩\s*){2,}", "⟨E⟩ ", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    # Deduplicate replaced list preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for r in replaced:
        k = r.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return out, uniq
