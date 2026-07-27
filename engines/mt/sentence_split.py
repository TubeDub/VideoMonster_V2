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
    src = str(source or "").strip()
    tr = str(translated or "").strip()
    if not tr:
        return True

    # CJK sources have few whitespace tokens — use character density.
    try:
        from engines.mt.cjk_meaning import cjk_char_count, is_cjk_heavy

        if is_cjk_heavy(src, min_chars=12):
            src_c = max(1, cjk_char_count(src))
            tr_letters = len(re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]", tr))
            if is_cjk_heavy(tr, min_chars=8):
                # Source script leaked into MT — treat as collapse
                return True
            # Very short Cyrillic vs long CJK blob
            if src_c >= 40 and tr_letters < max(12, int(src_c * 0.15)):
                return True
            return False
    except Exception:
        pass

    src_w = len(src.split())
    tr_w = len(tr.split())
    if src_w < min_src_words:
        return False
    return tr_w < max(8, int(src_w * min_ratio))
