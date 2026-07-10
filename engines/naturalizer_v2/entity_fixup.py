"""Correct wrong cross-entity substitutions after MT + restore."""

from __future__ import annotations

import re

from engines.naturalizer_v2.uk_name_forms import (
    GEORGE_JR_UK_GEN,
    GEORGE_JR_UK_NOM,
    GEORGE_LUCAS_UK,
    STAR_WARS_UK,
    USC_UK_NOM,
    apply_uk_dub_name_polish,
)


def _in_source(source: str, label: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", source, re.I))


def sanitize_wrong_entity_substitutions(
    text: str,
    *,
    original: str,
    tgt_lang: str = "uk",
) -> str:
    """
    Fix systematic wrong replacements (e.g. every token restored as George Lucas).
    Uses segment source only — never cross-segment entity guesses.
    """
    out = str(text or "")
    src = str(original or "")
    if not out.strip() or not src.strip():
        return out

    lang = (tgt_lang or "uk").split("-")[0].lower()
    has_jr = _in_source(src, "George Jr.") or _in_source(src, "George Jr")
    has_lucas = _in_source(src, "George Lucas")
    jr_repl = GEORGE_JR_UK_NOM if lang == "uk" else "George Jr."

    if has_jr and not has_lucas:
        out = re.sub(r"\bGeorge\s+Lucas\b", jr_repl, out, flags=re.I)
        out = re.sub(r"\bДжордж\s+Лукас\b", jr_repl, out, flags=re.I)

    if _in_source(src, "Fiat") and not has_lucas:
        out = re.sub(r"\bGeorge\s+Lucas\b", "Fiat", out, flags=re.I)

    if _in_source(src, "Haskell Wexler") and not has_lucas:
        repl = "Хаскелл Векслер" if lang == "uk" else "Haskell Wexler"
        out = re.sub(r"\bGeorge\s+Lucas\b", repl, out, flags=re.I)

    if _in_source(src, "University of Southern California"):
        uni = USC_UK_NOM if lang == "uk" else "University of Southern California"
        if "George Lucas" in out and not has_lucas:
            out = re.sub(r"\bGeorge\s+Lucas\b", uni, out, count=1, flags=re.I)

    if _in_source(src, "USC") and not _in_source(src, "Star Wars"):
        if re.search(r"\bStar Wars\b", out, re.I):
            repl = USC_UK_NOM if lang == "uk" else "USC"
            out = re.sub(r"\bStar Wars\b", repl, out, flags=re.I)

    if _in_source(src, "Hollywood") and lang == "uk":
        out = re.sub(
            r"\b(?:кінематографістом|оператором|кінооператором)\s+у\s+Джордж[\s-]молодш\w*\b",
            "кінооператором у Голлівуді",
            out,
            flags=re.I,
        )
        if "George Lucas" in out and not has_lucas and not has_jr:
            out = re.sub(r"\bGeorge\s+Lucas\b", "Голлівуд", out, count=1, flags=re.I)

    if _in_source(src, "Star Wars") and not has_lucas and lang == "uk":
        out = re.sub(r"\bGeorge\s+Lucas\b", STAR_WARS_UK, out, count=1, flags=re.I)

    if has_lucas and lang == "uk":
        out = re.sub(
            rf"(відом(?:ий|а|і)\s+як\s+)[«\"]?{re.escape(STAR_WARS_UK.strip('«»'))}[»\"]?",
            rf"\1{GEORGE_LUCAS_UK}",
            out,
            flags=re.I,
        )
        out = re.sub(r"\bGeorge\s+Lucas\b", GEORGE_LUCAS_UK, out, flags=re.I)
    elif has_jr and not has_lucas and lang == "uk":
        out = re.sub(r"\bGeorge\s+Lucas\b", GEORGE_JR_UK_NOM, out, flags=re.I)
        if re.search(r"\b(що|в|у)\s+George\s+Lucas\b", src, re.I):
            out = re.sub(
                r"\b" + re.escape(GEORGE_JR_UK_NOM) + r"\s+вилетіл[ао]\b",
                f"{GEORGE_JR_UK_GEN} викинуло",
                out,
                flags=re.I,
            )

    if lang == "uk":
        out = apply_uk_dub_name_polish(out, original=src)

    return out.strip()
