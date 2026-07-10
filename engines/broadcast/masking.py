"""Broadcast masking — replace entities with immutable [##ID##] tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass

from engines.broadcast.termbase import EntityKind, Termbase, TermEntry


@dataclass
class MaskResult:
    masked_text: str
    token_map: dict[str, int]  # canonical token -> term_id
    termbase: Termbase


def _catalog_entries(app_dir) -> list[tuple[str, EntityKind]]:
    from pathlib import Path

    base = app_dir or Path(__file__).resolve().parent.parent.parent
    builtin = [
        ("George Jr.", EntityKind.PERSON),
        ("George Lucas", EntityKind.PERSON),
        ("Star Wars", EntityKind.TITLE),
        ("Hollywood", EntityKind.PLACE),
        ("University of Southern California", EntityKind.ORG),
    ]
    out = list(builtin)
    seen = {t[0].lower() for t in out}
    try:
        from engines.proper_nouns_dict import (
            keep_latin_tokens,
            preferred_translations,
            transliterate_names,
        )

        for latin in keep_latin_tokens(base):
            if latin.lower() not in seen:
                out.append((latin, EntityKind.ORG))
                seen.add(latin.lower())
        for title in preferred_translations(base):
            if title.lower() not in seen:
                out.append((title, EntityKind.TITLE))
                seen.add(title.lower())
        for name in transliterate_names(base):
            if name.lower() not in seen:
                out.append((name, EntityKind.PERSON))
                seen.add(name.lower())
    except Exception:
        pass
    out.sort(key=lambda x: -len(x[0]))
    return out


def populate_termbase_from_text(text: str, termbase: Termbase, app_dir) -> None:
    src = str(text or "")
    for entity, kind in _catalog_entries(app_dir):
        pat = re.compile(r"(?<!\w)" + re.escape(entity) + r"(?!\w)", re.IGNORECASE)
        if pat.search(src):
            termbase.register(entity, kind)


def mask_text(text: str, termbase: Termbase) -> MaskResult:
    """Replace LOCKED entities with [##ID##] tokens (longest first)."""
    working = str(text or "")
    token_map: dict[str, int] = {}
    entries = sorted(termbase.all_locked(), key=lambda e: len(e.original), reverse=True)
    for entry in entries:
        if entry.original not in working:
            continue
        tok = entry.token()
        token_map[tok] = entry.term_id
        working = working.replace(entry.original, tok, 1)
    return MaskResult(masked_text=working, token_map=token_map, termbase=termbase)
