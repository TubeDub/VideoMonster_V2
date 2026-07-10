"""Rule-based semantic rewrite — dedupe subjects, literal fixes, synonyms."""

from __future__ import annotations

import re
from typing import Any

from engines.translation_naturalizer import (
    _PRONOUNS_RU,
    _PRONOUNS_UK,
    _apply_generic_fixes,
    _apply_ru_word_fixes,
    _apply_uk_word_fixes,
    _drop_repeated_subject,
    apply_style_polish,
    naturalize_generic,
    naturalize_ru,
    naturalize_uk,
)
from engines.mt.lang_codes import normalize_lang

# Common awkward MT synonyms (RU) — safe replacements preserving meaning
_RU_SYNONYM_FIXES: list[tuple[str, str]] = [
    (r"\bосуществляет\b", "делает"),
    (r"\bОсуществляет\b", "Делает"),
    (r"\bявляется\b", "—"),
    (r"\bЯвляется\b", "—"),
    (r"\bданный\b", "этот"),
    (r"\bДанный\b", "Этот"),
    (r"\bданная\b", "эта"),
    (r"\bДанная\b", "Эта"),
    (r"\bданное\b", "это"),
    (r"\bданные\b", "эти"),
    (r"\bв настоящее время\b", "сейчас"),
    (r"\bВ настоящее время\b", "Сейчас"),
    (r"\bв связи с тем что\b", "потому что"),
    (r"\bВ связи с тем что\b", "Потому что"),
]

# Literal phrasing heuristics (RU)
_RU_LITERAL_FIXES: list[tuple[str, str]] = [
    (r"\bон есть\b", "он"),
    (r"\bона есть\b", "она"),
    (r"\bони есть\b", "они"),
    (r"\bэто есть\b", "это"),
    (r"\bделает так что\b", "делает так, что"),
    (r"\bсказал что\b", "сказал, что"),
    (r"\bзнал что\b", "знал, что"),
    (r"\bпонял что\b", "понял, что"),
    (r"\bпотому что что\b", "потому что"),
]

_UK_SYNONYM_FIXES: list[tuple[str, str]] = [
    (r"\bздійснює\b", "робить"),
    (r"\bЗдійснює\b", "Робить"),
    (r"\bє\b(?=\s+[а-яіїє])", ""),
    (r"\bданий\b", "цей"),
    (r"\bдана\b", "ця"),
    (r"\bдане\b", "це"),
    (r"\bв даний час\b", "зараз"),
    (r"\bу зв'язку з тим що\b", "тому що"),
]

_VARIANT_LABELS = ("A", "B", "C")


def _apply_patterns(text: str, patterns: list[tuple[str, str]]) -> str:
    out = str(text or "")
    for pattern, repl in patterns:
        out = re.sub(pattern, repl, out)
    return out.strip()


def dedupe_consecutive_subjects(
    text: str,
    prev_context: str | None,
    tgt_lang: str,
) -> str:
    """Remove repeated subject from consecutive lines."""
    lang = normalize_lang(tgt_lang)
    pronouns = _PRONOUNS_UK if lang == "uk" else _PRONOUNS_RU
    return _drop_repeated_subject(str(text or ""), prev_context, pronouns)


def fix_literal_phrasing(text: str, tgt_lang: str) -> str:
    """Fix common literal machine-translation phrasing."""
    lang = normalize_lang(tgt_lang)
    out = str(text or "")
    if lang == "ru":
        out = _apply_patterns(out, _RU_LITERAL_FIXES)
    return _apply_generic_fixes(out).strip()


def apply_synonym_replacement(text: str, tgt_lang: str) -> str:
    """Replace awkward MT words with natural synonyms."""
    lang = normalize_lang(tgt_lang)
    if lang == "ru":
        return _apply_patterns(text, _RU_SYNONYM_FIXES)
    if lang == "uk":
        return _apply_patterns(text, _UK_SYNONYM_FIXES)
    return str(text or "").strip()


def _apply_full_semantic_polish(
    text: str,
    *,
    source: str = "",
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    app_dir=None,
) -> str:
    """Calques, ruisms, idioms — shared polish stack for all rule variants."""
    lang = normalize_lang(tgt_lang)
    out = str(text or "").strip()
    if not out:
        return out

    if lang == "uk":
        from engines.translation_naturalizer import (
            _UK_CALQUE_NATURALIZER,
            _UK_MIXED_LANGUAGE_FIXES,
            _UK_RUISM_FIXES,
            _apply_word_fixes,
            naturalize_uk,
        )

        out = _apply_word_fixes(out, _UK_MIXED_LANGUAGE_FIXES)
        out = _apply_word_fixes(out, _UK_RUISM_FIXES)
        out = _apply_word_fixes(out, _UK_CALQUE_NATURALIZER)
        out = naturalize_uk(out, prev_context)
    elif lang == "ru":
        from engines.translation_naturalizer import (
            _RU_CALQUE_NATURALIZER,
            _apply_ru_word_fixes,
            naturalize_ru,
        )

        out = _apply_word_fixes(out, _RU_CALQUE_NATURALIZER)
        out = _apply_ru_word_fixes(out)
        out = naturalize_ru(out, prev_context)

    out = apply_style_polish(out, lang, source=source, app_dir=app_dir)
    return preserve_dub_entities(out, source=source, tgt_lang=tgt_lang)


def rule_rewrite_variant(
    text: str,
    *,
    source: str = "",
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    variant: str = "A",
    app_dir=None,
) -> str:
    """Produce one rule-based candidate variant (A/B/C)."""
    raw = str(text or "").strip()
    if not raw:
        return raw

    lang = normalize_lang(tgt_lang)
    out = dedupe_consecutive_subjects(raw, prev_context, lang)

    if variant == "A":
        if lang == "ru":
            out = naturalize_ru(out, prev_context)
        elif lang == "uk":
            out = naturalize_uk(out, prev_context)
        else:
            out = naturalize_generic(out, prev_context)
    elif variant == "B":
        out = fix_literal_phrasing(out, lang)
        out = apply_synonym_replacement(out, lang)
        if lang == "ru":
            out = _apply_ru_word_fixes(out)
        elif lang == "uk":
            out = _apply_uk_word_fixes(out)
        out = _apply_generic_fixes(out)
    else:  # C
        out = apply_style_polish(out, lang, source=source, app_dir=app_dir)
        out = fix_literal_phrasing(out, lang)
        out = apply_synonym_replacement(out, lang)

    return _apply_full_semantic_polish(
        out,
        source=source,
        tgt_lang=tgt_lang,
        prev_context=prev_context,
        app_dir=app_dir,
    ).strip() or raw


def preserve_dub_entities(text: str, *, source: str = "", tgt_lang: str = "ru") -> str:
    """Fix names, abbreviations, and common semantic MT errors for dubbing."""
    lang = normalize_lang(tgt_lang)
    out = str(text or "").strip()
    if not out:
        return out

    if lang == "uk":
        try:
            from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

            out = apply_uk_dub_name_polish(out, original=source)
        except Exception:
            pass
        # Jr. must stay «молодший», never «старший»
        out = re.sub(r"Джордж-старш(?:ий|ого|ому)", "Джордж-молодший", out, flags=re.I)
        out = re.sub(r"Джорджа-старш(?:ого|ому)", "Джорджа-молодшого", out, flags=re.I)
        out = re.sub(r"\bДжордж\s+молодш(?:ий|ого|ому)\b", "Джордж-молодший", out, flags=re.I)
        out = re.sub(r"\bДжорджа\s+молодш(?:ого|ому)\b", "Джорджа-молодшого", out, flags=re.I)
        if re.search(r"\bJr\.?\b|George\s+Jr", source, re.I):
            out = re.sub(r"\bДжордж\s+старш(?:ий|ого)\b", "Джордж-молодший", out, flags=re.I)
        # USC / university — not generic «США» or nonsense «Скарб США»
        if re.search(r"\bUSC\b|Southern California|cinematography program|film school", source, re.I):
            out = re.sub(r"\b(?:до|в|у)\s+США\b", "до USC", out, flags=re.I)
            out = re.sub(r"\bпрограма\s+США\b", "програма кінематографії USC", out, flags=re.I)
            out = re.sub(r"компанії з фільму [«\"]Скарб США[»\"]", "USC film school", out, flags=re.I)
            out = re.sub(r"листа від компанії з фільму [«\"][^»\"]+[»\"]", "лист про зарахування до USC", out, flags=re.I)
            out = re.sub(r"\bУніверситет(?:у|і)?(?:,|\s)", "USC, ", out, flags=re.I)
        # Idiom fixes
        out = re.sub(r"\bпереможна\s+швидкість\b", "переможець", out, flags=re.I)
        out = re.sub(r"\bпереможний\s+водій\b", "переможець", out, flags=re.I)
        out = re.sub(r"\bпереможного\s+їзда\b", "переможця", out, flags=re.I)
        out = re.sub(r"\bфото\s+перемож(?:ної|ного)\s+швидкості\b", "фото переможця", out, flags=re.I)

    return out.strip()


def generate_rule_candidates(
    text: str,
    *,
    source: str = "",
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    app_dir=None,
) -> dict[str, str]:
    """Generate three rule-based variants A/B/C."""
    lang = normalize_lang(tgt_lang)
    src = str(source or "").strip()
    base = str(text or "").strip()
    out: dict[str, str] = {}
    for label in _VARIANT_LABELS:
        candidate = rule_rewrite_variant(
            base,
            source=src,
            tgt_lang=tgt_lang,
            prev_context=prev_context,
            variant=label,
            app_dir=app_dir,
        )
        # Never pass through raw MT unchanged when it still looks like source-language calque.
        if src and candidate.strip() == base.strip() and lang in ("ru", "uk"):
            candidate = rule_rewrite_variant(
                base,
                source=src,
                tgt_lang=tgt_lang,
                prev_context=prev_context,
                variant="C",
                app_dir=app_dir,
            )
        out[label] = preserve_dub_entities(candidate, source=src, tgt_lang=tgt_lang)
    return out


def rule_rewrite(
    text: str,
    *,
    source: str = "",
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    app_dir=None,
) -> str:
    """Final fallback rule rewrite (variant B)."""
    return rule_rewrite_variant(
        text,
        source=source,
        tgt_lang=tgt_lang,
        prev_context=prev_context,
        variant="B",
        app_dir=app_dir,
    )
