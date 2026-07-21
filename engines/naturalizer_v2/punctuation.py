"""Punctuation and sentence-ending cleanup."""

from __future__ import annotations

import re


def clean_punctuation(text: str) -> str:
    out = str(text or "").strip()
    if not out:
        return out

    # Collapse whitespace
    out = re.sub(r"\s+", " ", out)
    # Double/triple dots
    out = re.sub(r"\.{2,}", ".", out)
    # TZ v4.0 P2: «Фіат,.» / duplicated punct
    out = re.sub(r"([,;:])\s*\.", ".", out)
    out = re.sub(r"\.\s*([,;:])", ".", out)
    out = re.sub(r"([,.!?;:])\1+", r"\1", out)
    # Space before punctuation
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    # Missing space after punctuation
    out = re.sub(r"([,.!?;:])([^\s])", r"\1 \2", out)
    # Stray quotes
    out = re.sub(r'"{2,}', '"', out)
    out = re.sub(r"'{2,}", "'", out)
    # Trailing junk
    out = re.sub(r"[\s\-–—]+$", "", out)
    # Unclosed fragment — if ends mid-word with hyphen only, trim
    if re.search(r"\b\w+-\s*$", out):
        out = re.sub(r"-\s*$", "", out).strip()

    # Ensure sentence ends with punctuation if it looks complete
    if out and out[-1].isalnum() and len(out.split()) >= 4:
        if out[-1] in "аеиоуяюєіїь":
            out += "."

    return out.strip()
