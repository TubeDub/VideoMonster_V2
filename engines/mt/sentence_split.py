"""Sentence splitting that does not break on Jr. / Mr. / etc."""

from __future__ import annotations

import re

_ABBREV = re.compile(
    r"\b("
    r"Jr|Mr|Mrs|Ms|Dr|Prof|Sr|vs|etc|St|Mt|Ltd|Inc|Corp|Co|Univ|U\.S|U\.K|"
    r"No|Vol|Fig|Eq|approx|dept"
    r")\.",
    re.I,
)


def split_mt_sentences(text: str) -> list[str]:
    """Split on sentence boundaries without chopping name abbreviations."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    placeholders: dict[str, str] = {}

    def _protect(m: re.Match) -> str:
        key = f"__ABBR{len(placeholders)}__"
        placeholders[key] = m.group(0)
        return key

    protected = _ABBREV.sub(_protect, clean)
    parts = re.split(r"(?<=[.!?…])\s+", protected)
    out: list[str] = []
    for part in parts:
        restored = part
        for key, val in placeholders.items():
            restored = restored.replace(key, val)
        restored = restored.strip()
        if restored:
            out.append(restored)
    return out if out else [clean]


def is_severe_mt_collapse(
    source: str,
    translated: str,
    *,
    min_src_words: int = 20,
    min_ratio: float = 0.28,
) -> bool:
    """True when MT output covers far too little of a long source (meaning loss)."""
    src_w = len(str(source or "").split())
    tr_w = len(str(translated or "").split())
    if src_w < min_src_words:
        return False
    if not str(translated or "").strip():
        return True
    return tr_w < max(8, int(src_w * min_ratio))
