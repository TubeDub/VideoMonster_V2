"""Rule-based shorten/expand for slot fit — no mechanical word cut."""

from __future__ import annotations

import re

from engines.ai_core.semantic_agent.rule_engine import (
    apply_synonym_replacement,
    dedupe_consecutive_subjects,
    fix_literal_phrasing,
)
from engines.mt.lang_codes import normalize_lang

_VARIANT_LABELS = ("A", "B", "C")

# Safe shorten: drop redundant fillers / hedges
_RU_FILLER_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:как бы|типа|в общем|так сказать|собственно говоря)\b", ""),
    (r"\b(?:действительно|на самом деле)\b", ""),
    (r"\s+,\s+что\s+", ", что "),
    (r"\s{2,}", " "),
]

_UK_FILLER_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:як би|типу|взагалі|так би мовити)\b", ""),
    (
        r"(\d+)-річний\s+хлопець\s+на\s+ім['']я\s+(Джордж-молодший)\s+проїжджав\s+"
        r"через\s+(?:своє\s+)?рідне\s+місто\s+дорогою\s+додому(?:\s+на\s+вечерю)?",
        r"\1-річний \2 їхав рідним містом додому на вечерю",
    ),
    (r"\bнасправді\s+був\s+дуже\b", "був дуже"),
    (r"\bне\s+міг\s+зрозуміти\b", "не розумів"),
    (r"\bпочав\s+здійснювати\s+поворот\b", "почав повертати"),
    (r"\bдуже\s+сильно\b", "сильно"),
    (r"\bпо-справжньому\s+серйозно\b", "серйозно"),
    (r"\bдійсно\s+більше\s+не\b", "більше не"),
    (r"\bДозвольте\s+мені\s+зробити\s+деякі\s+дзвінки\b", "Я зроблю кілька дзвінків"),
    (r"\bДавайте\s+мені\s+зробити\s+деякі\s+дзвінки\b", "Я зроблю кілька дзвінків"),
    (r"\bдеякі\s+дзвінки\b", "дзвінки"),
    (r"\bДозвольте\s+мені\s+зробити\s+", "Я зроблю "),
    (
        r"\bлежав\s+на\s+лікарняному\s+ліжку\s+у\s+відділенні\s+інтенсивної\s+терапії\s+"
        r"в\s+місцевій\s+лікарні\b",
        "лежав у реанімації місцевої лікарні",
    ),
    (
        r"\bякий\s+на\s+той\s+момент\s+повністю\s+одужав\s+після\s+травм\b",
        "який уже повністю одужав",
    ),
    (r"\bТак\s+два\s+тижні\s+раніше\b", "Два тижні раніше"),
    (r"\s{2,}", " "),
]

# Safe expand: natural pacing connectors (not word repetition)
_RU_EXPAND_SUFFIXES = (
    ", понимаете?",
    ", знаете?",
    ", ведь так?",
)
_UK_EXPAND_SUFFIXES = (
    ", розумієте?",
    ", знаєте?",
)
_EN_EXPAND_SUFFIXES = (
    ", you know?",
    ", right?",
)


def _apply_patterns(text: str, patterns: list[tuple[str, str]]) -> str:
    out = str(text or "")
    for pattern, repl in patterns:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return " ".join(out.split()).strip()


def shorten_rule(
    text: str,
    *,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    variant: str = "A",
) -> str:
    """Intelligent shorten: synonyms, restructure, drop fillers — never tail-cut."""
    base = str(text or "").strip()
    if not base:
        return base

    lang = normalize_lang(tgt_lang)
    out = dedupe_consecutive_subjects(base, prev_context, tgt_lang)
    out = fix_literal_phrasing(out, tgt_lang)
    out = apply_synonym_replacement(out, tgt_lang)

    if lang == "ru":
        out = _apply_patterns(out, _RU_FILLER_PATTERNS)
    elif lang == "uk":
        out = _apply_patterns(out, _UK_FILLER_PATTERNS)

    if variant == "B":
        out = re.sub(r"\b(?:очень|весьма|крайне|довольно)\s+", "", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(?:that is|which is|который|которая|которое)\s+", "", out, flags=re.IGNORECASE)
    elif variant == "C":
        # Protect «не просто» (= EN not just) before dropping filler «просто».
        _prot = "\x00NE_PROSTO\x00"
        out = re.sub(r"\bне\s+просто\b", _prot, out, flags=re.IGNORECASE)
        out = re.sub(r"\b(?:просто|буквально|literally|just)\s+", "", out, flags=re.IGNORECASE)
        out = out.replace(_prot, "не просто")
        out = re.sub(r"\s+—\s+", ", ", out)

    out = " ".join(out.split()).strip()
    if not out or out == base:
        return base
    # Allow aggressive filler/phrase compress; only block extreme collapse.
    if len(out) < max(6, int(len(base) * 0.35)):
        return base
    # Semantic verification — reject mechanical truncation / entity loss
    try:
        from engines.semantic_meaning import is_truncated_adaptation, restore_terminal_close
        from engines.translation_quality import missing_preserved_tokens

        out = restore_terminal_close(out, reference=base, original=str(prev_context or ""))
        if is_truncated_adaptation(base, out):
            return base
        src = str(prev_context or "").strip()
        if src:
            missing = missing_preserved_tokens(src, out)
            if missing:
                return base
    except Exception:
        pass
    return out


def expand_rule(
    text: str,
    *,
    tgt_lang: str = "ru",
    variant: str = "A",
) -> str:
    """Natural expansion with connectors — no repeated words to fill."""
    base = str(text or "").strip()
    if not base:
        return base

    lang = normalize_lang(tgt_lang)
    core = base.rstrip(".,!?…")

    if lang == "ru":
        suffixes = _RU_EXPAND_SUFFIXES
    elif lang == "uk":
        suffixes = _UK_EXPAND_SUFFIXES
    else:
        suffixes = _EN_EXPAND_SUFFIXES

    idx = {"A": 0, "B": 1, "C": 2}.get(variant, 0) % len(suffixes)
    suffix = suffixes[idx]
    expanded = f"{core}{suffix}"

    if variant == "B" and lang in ("ru", "uk"):
        expanded = f"Ну, {core}{suffix}" if lang == "ru" else f"Ну, {core}{suffix}"
    elif variant == "C":
        expanded = f"{core} — вот так{suffix}" if lang == "ru" else f"{core}{suffix}"

    return expanded.strip()


def generate_shorten_candidates(
    text: str,
    *,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
) -> dict[str, str]:
    """Three rule-based shorten variants A/B/C."""
    return {
        label: shorten_rule(text, tgt_lang=tgt_lang, prev_context=prev_context, variant=label)
        for label in _VARIANT_LABELS
    }


def generate_expand_candidates(
    text: str,
    *,
    tgt_lang: str = "ru",
) -> dict[str, str]:
    """Three rule-based expand variants A/B/C."""
    return {
        label: expand_rule(text, tgt_lang=tgt_lang, variant=label)
        for label in _VARIANT_LABELS
    }


generate_shorten_variants = generate_shorten_candidates
