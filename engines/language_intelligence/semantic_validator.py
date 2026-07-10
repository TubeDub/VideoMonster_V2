"""Semantic Validator — meaning, entities, numbers, negation must be preserved."""

from __future__ import annotations

import re
from typing import Any

from engines.language_intelligence import rules as R


def _digits(text: str) -> set[str]:
    return set(re.findall(r"\d+", str(text or "")))


def _negation_markers(text: str) -> int:
    t = str(text or "").lower()
    markers = (
        " не ", " ні ", " немає", " без ", " not ", " no ", " never ", " neither ",
        " никогда ", " ніколи ",
    )
    return sum(1 for m in markers if m in f" {t} ")


def _extract_brands_and_names(original: str) -> set[str]:
    """Latin brands / acronyms that must stay recognizable — not every Title Case phrase."""
    found: set[str] = set()
    orig = str(original or "")
    ol = orig.lower()

    for latin in R.KEEP_LATIN:
        if re.search(r"(?<!\w)" + re.escape(latin) + r"(?!\w)", orig, re.I):
            found.add(latin.lower())

    for title in R.PREFERRED_UA_TITLES:
        if re.search(r"(?<!\w)" + re.escape(title) + r"(?!\w)", orig, re.I):
            found.add(title.lower())

    for name in R.TRANSLITERATE_NAMES:
        if name.lower() in ol:
            found.add(name.lower())

    for m in re.finditer(r"\b[A-Z]{2,}\b", orig):
        found.add(m.group(0).lower())

    return found


def _brand_preserved(brand: str, original: str, after: str) -> bool:
    al = after.lower()
    if brand in al:
        return True

    for src_title, ua_title in R.PREFERRED_UA_TITLES.items():
        if brand == src_title.lower():
            return ua_title.lower() in al

    for latin in R.KEEP_LATIN:
        if brand == latin.lower():
            if latin.lower() in al:
                return True
            for bad in R.CYRILLIC_MISTRANSLATIONS.get(latin, []):
                if bad.lower() in al:
                    return False
            return latin.lower() in al

    for src_name, tr_name in R.TRANSLITERATE_NAMES.items():
        if brand == src_name.lower():
            return tr_name.lower() in al

    if re.search(rf"\b{re.escape(brand.split()[0])}\b", after, re.I):
        return True

    return False


def _new_content_words(before: str, after: str) -> list[str]:
    bw = {w.lower() for w in re.findall(r"\b[\w'-]+\b", before) if len(w) > 2}
    aw = {w.lower() for w in re.findall(r"\b[\w'-]+\b", after) if len(w) > 2}
    return sorted(aw - bw)


def validate_semantic_preserve(
    original: str,
    before: str,
    after: str,
    *,
    preserved_tokens: list[str] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """
    After any fix — verify meaning signals preserved vs Original English.
    ``before`` (Raw MT / prior stage) is not used as a quality benchmark.
    Returns (ok, failure_reasons).
    """
    failures: list[dict[str, Any]] = []
    orig = str(original or "")
    a = str(after or "").strip()

    if not a:
        failures.append({"code": "empty_after"})
        return False, failures

    od, ad = _digits(orig), _digits(a)
    if od and not od.issubset(ad) and od != ad:
        missing = od - ad
        if missing:
            failures.append({"code": "numbers_changed", "detail": list(missing)[:5]})

    on, an = _negation_markers(orig), _negation_markers(a)
    if on > 0 and an < on:
        failures.append({"code": "negation_lost"})

    brands = _extract_brands_and_names(orig)
    for tok in preserved_tokens or []:
        brands.add(tok.lower())
    for brand in brands:
        if brand in orig.lower() and not _brand_preserved(brand, orig, a):
            if not re.search(r"[а-яіїє]", brand):
                failures.append({"code": "brand_lost", "token": brand})

    return len(failures) == 0, failures
