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

    # Ensure sentence ends with punctuation if it looks complete —
    # but never after a short clause that is clearly mid-thought
    # (e.g. «Але коли він їхав» before a following name clause).
    if out and out[-1].isalnum() and len(out.split()) >= 4:
        words = out.split()
        last = words[-1].lower().strip("«»\"'")
        mid_clause_tails = {
            "їхав",
            "їхала",
            "їхало",
            "їхали",
            "коли",
            "що",
            "який",
            "яка",
            "яке",
            "які",
            "але",
            "і",
            "та",
            "бо",
            "тож",
            "тому",
            "сказав",
            "сказала",
            "був",
            "була",
            "став",
            "стала",
            "міг",
            "могла",
            "хотів",
            "хотіла",
            "відчув",
            "відчула",
            "почав",
            "почала",
            "почали",
            "потім",
            "далі",
            "туди",
            "сюди",
        }
        if last not in mid_clause_tails and out[-1] in "аеиоуяюєіїь":
            out += "."

    # Undo false mid-sentence periods before a continuing proper name / clause
    # (soft_compress / naturalizer artifact: «їхав. Джордж»).
    out = re.sub(
        r"\b(їхав|їхала|їхали|сказав|сказала|був|була|став|стала|"
        r"міг|могла|хотів|хотіла|відчув|відчула|почав|почала|почали)\."
        r"\s+(?=[А-ЯІЇЄҐA-Z])",
        r"\1 ",
        out,
    )

    return out.strip()
