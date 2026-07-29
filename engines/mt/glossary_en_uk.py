# -*- coding: utf-8 -*-
"""EN→UK glossary for Simple MT (George Jr. / Lucas demo + common proper nouns).

Protects names before Marian and repairs known bad transliterations after.
Prefer project glossary (`data/glossaries/default_en_uk.json`) when available.
"""

from __future__ import annotations

import re
from typing import Iterable

# Fallback if project glossary is unavailable. Longer phrases first.
GLOSSARY_EN_UK: tuple[tuple[str, str], ...] = (
    ("University of Southern California", "USC"),
    ("George Junior", "Джордж-молодший"),
    ("George Jr.", "Джордж-молодший"),
    ("George Lucas", "Джордж Лукас"),
    ("Haskell Wexler", "Хаскелл Векслер"),
    ("Star Wars", "Зоряні війни"),
    ("Hollywood", "Голлівуд"),
    ("Fiat", "Фіат"),
    ("USC", "USC"),
)

# Post-MT repairs when Marian mangles protected or unprotected names.
_POST_FIXES: tuple[tuple[str, str], ...] = (
    (r"\bФайта\b", "Фіат"),
    (r"\bФайту\b", "Фіат"),
    (r"\bФайтом\b", "Фіатом"),
    (r"\bХаскелом Уекслером\b", "Хаскеллом Векслером"),
    (r"\bХаскел Уекслер\b", "Хаскелл Векслер"),
    (r"\bУекслер(?:ом|а|у)?\b", "Векслер"),
    (r"\bДжоржавськ\w*\b", "Джордж Лукас"),
    (r"\bлюдей\s+в\s+США\b", "людей в USC"),
    (r"\bподав(?:ся)?\s+до\s+США\b", "подався до USC"),
)


def glossary_pairs() -> tuple[tuple[str, str], ...]:
    """Longest-first EN→UK pairs (project glossary preferred)."""
    try:
        from engines.project_glossary import load_project_glossary

        g = load_project_glossary()
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for e in g.entries:
            labels = [e.source, *list(e.aliases or [])]
            for lab in labels:
                key = lab.strip()
                if not key or key.lower() in seen:
                    continue
                seen.add(key.lower())
                pairs.append((key, e.canonical))
        pairs.sort(key=lambda x: -len(x[0]))
        if pairs:
            return tuple(pairs)
    except Exception:
        pass
    return GLOSSARY_EN_UK


def _token(i: int) -> str:
    return f"⟦G{i}⟧"


def protect_glossary(
    text: str, *, pairs: Iterable[tuple[str, str]] | None = None
) -> tuple[str, list[str]]:
    """Replace EN glossary sources with placeholders; return (text, uk_forms)."""
    pairs = tuple(pairs or glossary_pairs())
    out = str(text or "")
    forms: list[str] = []
    for en, uk in pairs:
        if not en or en not in out:
            continue
        idx = len(forms)
        forms.append(uk)
        out = out.replace(en, _token(idx))
    return out, forms


def restore_glossary(text: str, forms: list[str]) -> str:
    out = str(text or "")
    for i, uk in enumerate(forms):
        out = out.replace(_token(i), uk)
        # Marian sometimes strips brackets / adds spaces around placeholder
        out = out.replace(f"[G{i}]", uk)
        out = out.replace(f"(G{i})", uk)
        out = re.sub(rf"\bG{i}\b", uk, out)
    return out


def apply_post_mt_glossary_fixes(text: str) -> str:
    out = str(text or "")
    for pat, repl in _POST_FIXES:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out


def apply_glossary_en_uk(text: str) -> str:
    """Best-effort post-MT glossary apply (no placeholders)."""
    out = apply_post_mt_glossary_fixes(text)
    for en, uk in glossary_pairs():
        if en in out:
            out = out.replace(en, uk)
    return out


def translate_with_glossary_protect(text: str, translate_fn) -> str:
    """Protect glossary → translate → restore → post-fixes."""
    protected, forms = protect_glossary(text)
    raw = str(translate_fn(protected) or "").strip()
    restored = restore_glossary(raw, forms)
    return apply_glossary_en_uk(restored)


def finalize_mt_text(src_lang: str, tgt_lang: str, text: str) -> str:
    """Post-process MT / cache-hit text (EN→UK glossary fixes). Safe no-op otherwise."""
    src = str(src_lang or "").strip().lower()
    tgt = str(tgt_lang or "").strip().lower()
    out = str(text or "")
    if src != "en" or tgt != "uk" or not out.strip():
        return out
    out = apply_post_mt_glossary_fixes(out)
    out = apply_glossary_en_uk(out)
    return out
