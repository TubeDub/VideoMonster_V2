"""Rule-based grammar fixes — punctuation, RU/UK patterns, MT artifacts."""

from __future__ import annotations

import re

from engines.mt.lang_codes import normalize_lang

_VARIANT_LABELS = ("A", "B", "C")

# RU punctuation / grammar
_RU_PUNCT_FIXES: list[tuple[str, str]] = [
    (r"\s+([,.!?;:])", r"\1"),
    (r"([а-яёА-ЯЁa-zA-Z0-9])([?!])", r"\1\2"),
    (r"\bсказал что\b", "сказал, что"),
    (r"\bСказал что\b", "Сказал, что"),
    (r"\bзнал что\b", "знал, что"),
    (r"\bпонял что\b", "понял, что"),
    (r"\bувидел что\b", "увидел, что"),
    (r"\bпотому что что\b", "потому что"),
    (r"\bчто бы\b", "чтобы"),
    (r"\s{2,}", " "),
]

_RU_GRAMMAR_FIXES: list[tuple[str, str]] = [
    (r"\b(?:он|она|оно|они)\s+есть\b", lambda m: m.group(0).split()[0]),
    (r"\b(?:этот|эта|это|эти)\s+самый\b", ""),
    (r"\bв данный момент\b", "сейчас"),
    (r"\bв настоящее время\b", "сейчас"),
    (r"\bявляется\b", "—"),
    (r"\bосуществляет\b", "делает"),
    (r"\bданный\b", "этот"),
    (r"\bданная\b", "эта"),
    (r"\bданное\b", "это"),
    (r"\bданные\b", "эти"),
]

# UK patterns
_UK_PUNCT_FIXES: list[tuple[str, str]] = [
    (r"\s+([,.!?;:])", r"\1"),
    (r"\bсказав що\b", "сказав, що"),
    (r"\bсказала що\b", "сказала, що"),
    (r"\bтому що що\b", "тому що"),
    (r"\s{2,}", " "),
]

_UK_GRAMMAR_FIXES: list[tuple[str, str]] = [
    (r"\bв даний час\b", "зараз"),
    (r"\bздійснює\b", "робить"),
    (r"\bданий\b", "цей"),
    (r"\bдана\b", "ця"),
    (r"\bдане\b", "це"),
]

# Common machine-translation calques
_MT_PATTERNS_RU: list[tuple[str, str]] = [
    (r"\bделает так что\b", "делает так, что"),
    (r"\bон пошел\b", "он пошёл"),
    (r"\bона пошла\b", "она пошла"),
    (r"\bне может быть\b", "не может"),
    (r"\bна данный момент\b", "сейчас"),
    (r"\bв связи с тем что\b", "потому что"),
]

_MT_PATTERNS_UK: list[tuple[str, str]] = [
    (r"\bу зв'язку з тим що\b", "тому що"),
    (r"\bна даний момент\b", "зараз"),
    (r"\bвін пішов\b", "він пішов"),
    (r"\bехав\b", "їхав"),
    (r"\bЕхав\b", "Їхав"),
    (r"\bреанимаблі\b", "реанімації"),
    (r"\bувivi\b", "вижив"),
    (r"\bне\s+мог\b", "не міг"),
    (r"\bне\s+могла\b", "не могла"),
    (r"\bвигнали\b", "викинули"),
    (r"\bGeorge\s+Jr\.?\b", "Джордж-молодший"),
]


def _apply_patterns(text: str, patterns: list[tuple[str, str]]) -> str:
    out = str(text or "")
    for pattern, repl in patterns:
        out = re.sub(pattern, repl, out)
    return out.strip()


def _apply_grammar_patterns(text: str, patterns: list) -> str:
    out = str(text or "")
    for item in patterns:
        pattern, repl = item
        if callable(repl):
            out = re.sub(pattern, repl, out)
        else:
            out = re.sub(pattern, repl, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def fix_punctuation(text: str, tgt_lang: str) -> str:
    lang = normalize_lang(tgt_lang)
    out = str(text or "").strip()
    if not out:
        return out
    patterns = _UK_PUNCT_FIXES if lang == "uk" else _RU_PUNCT_FIXES
    out = _apply_patterns(out, patterns)
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    if out and not re.search(r"[.!?…]$", out):
        out = out + "."
    return out.strip()


def fix_grammar(text: str, tgt_lang: str) -> str:
    lang = normalize_lang(tgt_lang)
    out = str(text or "").strip()
    if lang == "uk":
        out = _apply_grammar_patterns(out, _UK_GRAMMAR_FIXES)
        out = _apply_patterns(out, _MT_PATTERNS_UK)
    else:
        out = _apply_grammar_patterns(out, _RU_GRAMMAR_FIXES)
        out = _apply_patterns(out, _MT_PATTERNS_RU)
    return out.strip()


def fix_syntax(text: str, tgt_lang: str) -> str:
    """Light syntax polish — comma insertion, duplicate words."""
    out = str(text or "").strip()
    out = re.sub(r"\b(\w+)\s+\1\b", r"\1", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    out = re.sub(r"([а-яёіїєА-ЯЁІЇЄa-zA-Z])([?!])", r"\1\2", out)
    return out.strip()


def fix_style(text: str, tgt_lang: str) -> str:
    """Style polish without changing length significantly."""
    out = fix_grammar(text, tgt_lang)
    out = fix_punctuation(out, tgt_lang)
    return out.strip()


def rule_rewrite_variant(
    text: str,
    *,
    tgt_lang: str = "ru",
    variant: str = "A",
) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw

    if variant == "A":
        out = fix_punctuation(raw, tgt_lang)
    elif variant == "B":
        out = fix_grammar(raw, tgt_lang)
        out = fix_punctuation(out, tgt_lang)
    else:
        out = fix_syntax(raw, tgt_lang)
        out = fix_style(out, tgt_lang)

    return out.strip() or raw


def generate_rule_candidates(
    text: str,
    *,
    tgt_lang: str = "ru",
) -> dict[str, str]:
    return {
        label: rule_rewrite_variant(text, tgt_lang=tgt_lang, variant=label)
        for label in _VARIANT_LABELS
    }


def apply_grammar_pass(text: str, *, tgt_lang: str = "ru") -> str:
    """Pass 1 — grammar correction."""
    return fix_grammar(fix_punctuation(text, tgt_lang), tgt_lang)


def apply_syntax_pass(text: str, *, tgt_lang: str = "ru") -> str:
    """Pass 2 — syntax improvement."""
    return fix_syntax(text, tgt_lang)


def apply_style_pass(text: str, *, tgt_lang: str = "ru") -> str:
    """Pass 3 — style improvement."""
    return fix_style(text, tgt_lang)
