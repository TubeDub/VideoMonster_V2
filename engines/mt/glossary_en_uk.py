# -*- coding: utf-8 -*-
"""EN→UK glossary for Simple MT — Stage 14b: POST-MT only (no protect before Marian).

Protect/restore are no-ops. Marian sees raw English; names are fixed after MT.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger("tubedub.mt.glossary")

# Fallback pairs (longest first). Used by post-MT apply.
GLOSSARY_EN_UK: tuple[tuple[str, str], ...] = (
    ("University of Southern California", "Університет Південної Каліфорнії"),
    ("George Junior", "Джордж Молодший"),
    ("George Jr.", "Джордж Молодший"),
    ("George Jr", "Джордж Молодший"),
    ("George Lucas", "Джордж Лукас"),
    ("Haskell Wexler", "Хаскелл Векслер"),
    ("Star Wars", "Зоряні війни"),
    ("Hollywood", "Голлівуд"),
    ("Wexler", "Векслер"),
    ("Fiat", "Фіат"),
    ("USC", "USC"),
    ("George", "Джордж"),
)

# Marian mangling repairs (UK side).
_POST_UK_FIXES: tuple[tuple[str, str], ...] = (
    (r"\bФайта\b", "Фіат"),
    (r"\bФайту\b", "Фіат"),
    (r"\bФайтом\b", "Фіатом"),
    (r"\bХаскелом\s+Уекслером\b", "Хаскеллом Векслером"),
    (r"\bХаскел\s+Уекслер\b", "Хаскелл Векслер"),
    (r"\bУекслер(?:ом|а|у)?\b", "Векслер"),
    (r"\bДжоржавськ\w*\b", "Джордж Лукас"),
    (r"\bлюдей\s+в\s+США\b", "людей в USC"),
    (r"\bподав(?:ся)?\s+до\s+США\b", "подався до USC"),
)

# Leftover protect-token garbage (Marian-mangled + legacy).
_GARBAGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"__GLOS[_A-Z0-9_*\s]*", re.I),
    re.compile(r"_GLOS[_A-Z0-9_*\s]*", re.I),
    re.compile(r"__GLOSS_\d+__", re.I),
    re.compile(r"\bGLOSS_?\d+\b", re.I),
    re.compile(r"\}[A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9_-]+"),
    re.compile(r"\{\s*[A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9_-]+"),
    re.compile(r"\bG\d{2,}\b"),
    re.compile(r"\bГГ?\d+\b", re.I),
    re.compile(r"ведьг\d*\]?", re.I),
    re.compile(r"⟦\s*G?\d*\s*⟧", re.I),
    re.compile(r"\[\s*G\d+\s*\]", re.I),
    re.compile(r"\(\s*G\d+\s*\)", re.I),
)

_PLACEHOLDER_RE = re.compile(r"__?GLOS_|\}[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]", re.I)


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


def protect_glossary(
    text: str, *, pairs: Iterable[tuple[str, str]] | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Stage 14b: Simple no-op — Marian gets RAW source (no placeholders)."""
    _ = pairs
    return str(text or ""), []


def restore_glossary(
    text: str,
    forms: list[str] | list[tuple[str, str]],
) -> str:
    """Stage 14b: no-op when map empty (protect disabled)."""
    if not forms:
        return str(text or "")
    # Legacy path only — strip leftovers if somehow called with a map.
    return strip_glossary_placeholders(str(text or ""))


def strip_glossary_placeholders(text: str) -> str:
    """Remove leftover / Marian-mangled glossary placeholders."""
    out = str(text or "")
    for pat in _GARBAGE_PATTERNS:
        out = pat.sub(" ", out)
    out = re.sub(r"\}\s*", " ", out)
    out = re.sub(r"\{\s*", " ", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip(" ,;:")


# Alias used by older call sites / Stage 14.
strip_glossary_artifacts = strip_glossary_placeholders


def contains_glossary_garbage(text: str) -> bool:
    t = str(text or "")
    if not t:
        return False
    if _PLACEHOLDER_RE.search(t):
        return True
    for pat in _GARBAGE_PATTERNS:
        if pat.search(t):
            return True
    if re.search(r"ведьг", t, re.I):
        return True
    return False


def _replace_en_phrase(text: str, en: str, uk: str) -> str:
    """Case-insensitive, word-boundary-ish EN→UK replace."""
    if not en or not uk:
        return text
    # Flexible whitespace inside multi-word phrases.
    body = re.escape(en)
    body = body.replace(r"\ ", r"\s+")
    # Allow optional trailing period already in en (Jr.) — escaped.
    pat = re.compile(rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])", re.IGNORECASE)
    return pat.sub(uk, text)


def apply_post_mt_glossary_fixes(text: str) -> str:
    """EN→UK glossary + Marian UK mangling repairs (longest EN first)."""
    out = str(text or "")
    # Prefer built-in order for Jr/Lucas/George precedence, then project pairs.
    seen_lower: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for en, uk in GLOSSARY_EN_UK:
        key = en.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        ordered.append((en, uk))
    for en, uk in glossary_pairs():
        key = en.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        ordered.append((en, uk))
    ordered.sort(key=lambda x: -len(x[0]))
    for en, uk in ordered:
        out = _replace_en_phrase(out, en, uk)
    for pat, repl in _POST_UK_FIXES:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out


def apply_glossary_en_uk(text: str) -> str:
    """Best-effort post-MT glossary apply (no placeholders)."""
    return apply_post_mt_glossary_fixes(text)


def translate_with_glossary_protect(text: str, translate_fn) -> str:
    """Stage 14b: raw translate → finalize (protect is no-op)."""
    raw = str(translate_fn(str(text or "")) or "").strip()
    return finalize_mt_text("en", "uk", raw)


def finalize_mt_text(src_lang: str, tgt_lang: str, text: str) -> str:
    """Post-process MT / cache-hit text. Strip placeholders → glossary → strip."""
    src = str(src_lang or "").strip().lower()
    tgt = str(tgt_lang or "").strip().lower()
    out = str(text or "")
    if not out.strip():
        return out
    out = strip_glossary_placeholders(out)
    if src == "en" and tgt == "uk":
        out = apply_post_mt_glossary_fixes(out)
    out = strip_glossary_placeholders(out)
    if contains_glossary_garbage(out):
        logger.error("[glossary] placeholders remain after finalize: %r", out[:120])
        out = strip_glossary_placeholders(out)
    return out
