"""
Semantic translation helpers: idiom-by-meaning rules and literal-calque detection.
Language-agnostic architecture with per-target-language pattern tables.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "apply_semantic_polish_lines",
    "detect_semantic_issues",
    "semantic_quality_warnings",
]

# Known literal calques in target text (regex → hint code)
_LITERAL_CALQUES: dict[str, list[tuple[str, str]]] = {
    "ru": [
        (r"\bделает\s+смысл\b", "make_sense"),
        (r"\bделать\s+смысл\b", "make_sense"),
        (r"\bимеет\s+место\s+быть\b", "take_place"),
        (r"\bбрать\s+место\b", "take_place"),
        (r"\bберёт\s+место\b", "take_place"),
        (r"\bна\s+другой\s+стороне\s+руки\b", "on_other_hand"),
        (r"\bв\s+конечном\s+итоге\b", "at_end_of_day"),
        (r"\bкогда\s+всё\s+сказано\s+и\s+сделано\b", "when_all_said"),
        (r"\bэто\s+поворачивается\b", "turns_out"),
        (r"\bоказывается\s+поворачивается\b", "turns_out"),
        (r"\bломать\s+лёд\b", "break_ice"),
        (r"\bкусок\s+торта\b", "piece_of_cake"),
        (r"\bне\s+имеет\s+значения\b", "doesnt_matter_calque"),
        (r"\bв\s+течение\s+времени\b", "over_time_calque"),
        (r"\bв\s+данный\s+момент\s+времени\b", "at_this_moment"),
        (r"\bс\s+точки\s+зрения\s+.*?\s+стороны\b", "from_pov_calque"),
    ],
    "uk": [
        (r"\bробить\s+сенс\b", "make_sense"),
        (r"\bробити\s+сенс\b", "make_sense"),
        (r"\bмати\s+місце\s+бути\b", "take_place"),
        (r"\bбрати\s+місце\b", "take_place"),
        (r"\bз\s+іншого\s+боку\s+руки\b", "on_other_hand"),
        (r"\bв\s+кінцевому\s+підсумку\b", "at_end_of_day"),
        (r"\bце\s+повертається\b", "turns_out"),
        (r"\bлама(ти|є)\s+лід\b", "break_ice"),
        (r"\bшматок\s+торта\b", "piece_of_cake"),
        (r"\bна\s+даний\s+момент\s+часу\b", "at_this_moment"),
        (r"\bв\s+даний\s+момент\b", "at_this_moment"),
        (r"\bне\s+має\s+значення\b", "doesnt_matter_calque"),
        (r"\bз\s+точки\s+зору\b", "from_pov_calque"),
    ],
    "en": [
        (r"\bgive\s+a\s+look\b", "give_look_calque"),
        (r"\bdo\s+a\s+decision\b", "do_decision_calque"),
        (r"\bhave\s+the\s+possibility\b", "have_possibility_calque"),
        (r"\bin\s+the\s+actual\s+moment\b", "at_this_moment"),
    ],
    "de": [
        (r"\bmacht\s+sinn\b", "make_sense_de"),
        (r"\bim\s+Endeffekt\b", "at_end_of_day"),
    ],
    "fr": [
        (r"\bil\s+fait\s+sens\b", "make_sense_fr"),
        (r"\bau\s+final\s+de\s+la\s+journée\b", "at_end_of_day"),
    ],
    "es": [
        (r"\bhace\s+sentido\b", "make_sense_es"),
        (r"\btomar\s+lugar\b", "take_place"),
    ],
}

# Source idiom cues → expected bad literal markers in target
_SOURCE_IDIOM_CUES: list[tuple[str, dict[str, list[str]]]] = [
    (
        r"\bmake\s+sense\b",
        {"ru": [r"делать\s+смысл", r"делает\s+смысл"], "uk": [r"робить\s+сенс"]},
    ),
    (
        r"\btake\s+place\b",
        {"ru": [r"брать\s+место", r"берёт\s+место"], "uk": [r"брати\s+місце"]},
    ),
    (
        r"\bon\s+the\s+other\s+hand\b",
        {"ru": [r"на\s+другой\s+стороне"], "uk": [r"з\s+іншого\s+боку\s+руки"]},
    ),
    (
        r"\bat\s+the\s+end\s+of\s+the\s+day\b",
        {"ru": [r"в\s+конечном\s+итоге"], "uk": [r"в\s+кінцевому\s+підсумку"]},
    ),
    (
        r"\bit\s+turns\s+out\b|\bturns\s+out\b",
        {"ru": [r"поворачивается", r"оказывается\s+поворачивается"], "uk": [r"повертається"]},
    ),
    (
        r"\bpiece\s+of\s+cake\b",
        {"ru": [r"кусок\s+торта"], "uk": [r"шматок\s+торта"]},
    ),
    (
        r"\bbreak\s+the\s+ice\b",
        {"ru": [r"ломать\s+лёд", r"ломает\s+лёд"], "uk": [r"лама(ти|є)\s+лід"]},
    ),
    (
        r"\bonce\s+in\s+a\s+blue\s+moon\b",
        {"ru": [r"раз\s+в\s+синюю\s+луну"], "uk": [r"раз\s+у\s+блакитний\s+місяць"]},
    ),
]

# Rule-based calque fixes (applied after naturalizer)
_CALQUE_FIXES: dict[str, list[tuple[str, str]]] = {
    "ru": [
        (r"\bделает\s+смысл\b", "имеет смысл"),
        (r"\bделать\s+смысл\b", "иметь смысл"),
        (r"\bбрать\s+место\b", "происходит"),
        (r"\bберёт\s+место\b", "происходит"),
        (r"\bимеет\s+место\s+быть\b", "происходит"),
        (r"\bэто\s+поворачивается\b", "оказывается"),
        (r"\bоказывается\s+поворачивается\b", "оказывается"),
        (r"\bв\s+данный\s+момент\s+времени\b", "сейчас"),
        (r"\bв\s+течение\s+времени\b", "со временем"),
        (r"\bкусок\s+торта\b", "проще простого"),
    ],
    "uk": [
        (r"\bробить\s+сенс\b", "має сенс"),
        (r"\bробити\s+сенс\b", "мати сенс"),
        (r"\bбрати\s+місце\b", "відбувається"),
        (r"\bбере\s+місце\b", "відбувається"),
        (r"\bмати\s+місце\s+бути\b", "відбувається"),
        (r"\bце\s+повертається\b", "виявляється"),
        (r"\bшматок\s+торта\b", "легко"),
        (r"\bна\s+даний\s+момент\s+часу\b", "зараз"),
        (r"\bна\s+даний\s+момент\b", "зараз"),
        (r"\bз\s+іншого\s+боку\s+руки\b", "з іншого боку"),
    ],
    "en": [
        (r"\bgive\s+a\s+look\b", "take a look"),
        (r"\bdo\s+a\s+decision\b", "make a decision"),
        (r"\bhave\s+the\s+possibility\b", "can"),
    ],
}


from engines.utils.lang_utils import normalize_lang as _normalize_lang


def _match_any(patterns: list[str], text: str) -> bool:
    for pat in patterns:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True
    return False


def detect_semantic_issues(
    source: str,
    translated: str,
    *,
    source_lang: str | None = None,
    target_lang: str | None = None,
) -> list[dict[str, Any]]:
    """Return semantic QA issues (literal calques, idiom mishandling)."""
    issues: list[dict[str, Any]] = []
    src = str(source or "")
    tr = str(translated or "").strip()
    if not tr:
        return issues

    tgt = _normalize_lang(target_lang)
    src_lang = _normalize_lang(source_lang)

    for pattern, code in _LITERAL_CALQUES.get(tgt, []):
        if re.search(pattern, tr, flags=re.IGNORECASE):
            issues.append({"code": "literal_construction", "detail": code, "stage": "semantic"})

    if src_lang == "en" or re.search(r"[a-zA-Z]{3,}", src):
        for src_pat, bad_by_tgt in _SOURCE_IDIOM_CUES:
            if not re.search(src_pat, src, flags=re.IGNORECASE):
                continue
            bad_patterns = bad_by_tgt.get(tgt, [])
            if bad_patterns and _match_any(bad_patterns, tr):
                issues.append({"code": "idiom", "detail": src_pat.strip("\\b"), "stage": "semantic"})
            elif tgt in bad_by_tgt and bad_by_tgt[tgt] and not bad_patterns:
                issues.append({"code": "idiom", "detail": "check_idiom", "stage": "semantic"})

    return issues


def semantic_quality_warnings(
    source: str,
    translated: str,
    *,
    source_lang: str | None = None,
    target_lang: str | None = None,
) -> list[dict[str, Any]]:
    return detect_semantic_issues(
        source,
        translated,
        source_lang=source_lang,
        target_lang=target_lang,
    )


def apply_semantic_polish_line(text: str, *, target_lang: str | None = None) -> str:
    """Rule-based fix for common literal calques in one line."""
    from engines.semantic_meaning import apply_compact_phrases

    out = str(text or "").strip()
    if not out:
        return out
    out = apply_compact_phrases(out, target_lang=target_lang)
    tgt = _normalize_lang(target_lang)
    for pattern, repl in _CALQUE_FIXES.get(tgt, []):
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out.strip()


def apply_semantic_polish_lines(
    lines: list[str],
    *,
    target_lang: str | None = None,
    source_segments: list[str] | None = None,
) -> list[str]:
    from engines.translation_quality import keep_if_not_worse

    src = list(source_segments) if source_segments else [""] * len(lines)
    out: list[str] = []
    for i, line in enumerate(lines):
        before = str(line or "").strip()
        after = apply_semantic_polish_line(before, target_lang=target_lang)
        original = str(src[i] if i < len(src) else "")
        out.append(keep_if_not_worse(before, after, original=original))
    return out
