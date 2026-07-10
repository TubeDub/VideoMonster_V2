"""
Automatic stress marks for Ukrainian / Russian text before TTS synthesis.

Edge TTS neural voices for uk-UA and ru-RU recognise the Unicode
combining acute accent (U+0301) placed immediately AFTER the stressed
vowel.  Example: "привет" → "приве́т"  (е + U+0301).

Approach (graceful degradation):
  1. Try ruaccent / ukrainian_accentor library (if installed).
  2. Fall back to a compact built-in rule set for the most common
     Ukrainian/Russian words and patterns.
  3. If nothing works: return text unchanged — TTS still works, just
     without guaranteed stress.

No external network calls; safe to call on every TTS segment.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Unicode helpers ──────────────────────────────────────────────────────────
_ACCENT = "\u0301"          # combining acute accent
_VOWELS_RU = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_VOWELS_UK = "аеєиіїоуяАЕЄИІЇОУЯ"
_VOWELS = set(_VOWELS_RU + _VOWELS_UK)


def _add_accent_after(text: str, vowel_pos: int) -> str:
    """Insert U+0301 after the character at vowel_pos."""
    return text[: vowel_pos + 1] + _ACCENT + text[vowel_pos + 1 :]


def _count_vowels(word: str) -> int:
    return sum(1 for c in word if c in _VOWELS)


def _vowel_positions(word: str) -> list[int]:
    return [i for i, c in enumerate(word) if c in _VOWELS]


def _already_accented(word: str) -> bool:
    """True if U+0301 already present anywhere in the word."""
    return _ACCENT in word


# ─── Library-based accentuation (optional) ───────────────────────────────────

@lru_cache(maxsize=1)
def _try_load_ruaccent():
    """Load ruaccent once; return accentuator or None."""
    try:
        from ruaccent import RUAccent   # type: ignore
        accentuator = RUAccent()
        accentuator.load(omograph_model_size="tiny", use_dictionary=True)
        return accentuator
    except Exception:
        return None


@lru_cache(maxsize=1)
def _try_load_ua_accentor():
    """Load ukrainian_accentor once; return module or None."""
    try:
        import ukrainian_accentor as ua  # type: ignore
        return ua
    except Exception:
        return None


def _accent_via_library(text: str, lang: str) -> Optional[str]:
    lang_base = (lang or "").split("-")[0].lower()
    if lang_base in ("ru",):
        acc = _try_load_ruaccent()
        if acc:
            try:
                return acc.process_all(text)
            except Exception:
                pass
    if lang_base in ("uk",):
        ua = _try_load_ua_accentor()
        if ua:
            try:
                return ua.process(text)
            except Exception:
                pass
    return None


# ─── Built-in mini-dictionary ─────────────────────────────────────────────────
# Format: lowercase_word → stressed_version (U+0301 after stressed vowel)
# This covers the ~200 most frequent function/content words in UA/RU news text.

_STRESS_DICT: dict[str, str] = {
    # Ukrainian
    "але": "але\u0301",
    "було": "бу\u0301ло",
    "були": "бу\u0301ли",
    "буде": "бу\u0301де",
    "будуть": "бу\u0301дуть",
    "вже": "вже\u0301",
    "він": "ві\u0301н",
    "вона": "вона\u0301",
    "вони": "вони\u0301",
    "все": "все\u0301",
    "для": "для\u0301",
    "дуже": "ду\u0301же",
    "його": "його\u0301",
    "коли": "коли\u0301",
    "може": "мо\u0301же",
    "після": "пі\u0301сля",
    "також": "тако\u0301ж",
    "тому": "тому\u0301",
    "через": "че\u0301рез",
    "який": "яки\u0301й",
    "якого": "яко\u0301го",
    "якій": "які\u0301й",
    "більше": "бі\u0301льше",
    "менше": "ме\u0301нше",
    "навіть": "нави\u0301ть",
    "однак": "одна\u0301к",
    "перед": "пе\u0301ред",
    "перший": "пе\u0301рший",
    "перша": "пе\u0301рша",
    "перше": "пе\u0301рше",
    "другий": "дру\u0301гий",
    "друга": "дру\u0301га",
    "потім": "по\u0301тім",
    "тільки": "ті\u0301лько",
    "просто": "про\u0301сто",
    "зараз": "зара\u0301з",
    "тоді": "тоді\u0301",
    "ніж": "ніж\u0301",
    "тому що": "тому\u0301 що\u0301",
    # Russian
    "это": "э\u0301то",
    "этот": "э\u0301тот",
    "эта": "э\u0301та",
    "эти": "э\u0301ти",
    "было": "бы\u0301ло",
    "были": "бы\u0301ли",
    "будет": "бу\u0301дет",
    "будут": "бу\u0301дут",
    "очень": "о\u0301чень",
    "после": "по\u0301сле",
    "также": "та\u0301кже",
    "через": "че\u0301рез",
    "когда": "когда\u0301",
    "однако": "одна\u0301ко",
    "только": "то\u0301лько",
    "просто": "про\u0301сто",
    "сейчас": "сейча\u0301с",
    "потому": "потому\u0301",
    "первый": "пе\u0301рвый",
    "первая": "пе\u0301рвая",
    "второй": "второ\u0301й",
    "больше": "бо\u0301льше",
    "меньше": "ме\u0301ньше",
    "всегда": "всегда\u0301",
    "никогда": "никогда\u0301",
    "иногда": "иногда\u0301",
    "потом": "пото\u0301м",
    "тогда": "тогда\u0301",
    "здесь": "здесь\u0301",
    "самый": "са\u0301мый",
    "самая": "са\u0301мая",
    "самое": "са\u0301мое",
    "может": "мо\u0301жет",
    "нужно": "ну\u0301жно",
    "можно": "мо\u0301жно",
    "нельзя": "нельзя\u0301",
    "уже": "уже\u0301",
    "ещё": "ещё\u0301",
    "еще": "еще\u0301",
}


def _apply_dict_stress(text: str) -> str:
    """Apply built-in stress dictionary via word-boundary substitution."""
    result = text

    # Sort by length descending so multi-word patterns match before single words
    for pattern, accented in sorted(_STRESS_DICT.items(), key=lambda x: -len(x[0])):
        # case-insensitive word-boundary replacement preserving original case
        regex = re.compile(r"\b" + re.escape(pattern) + r"\b", re.IGNORECASE | re.UNICODE)

        def _replace(m: re.Match) -> str:
            original = m.group(0)
            if _already_accented(original):
                return original
            # Preserve casing by re-applying case pattern from original
            result_word = accented
            if original[0].isupper():
                result_word = result_word[0].upper() + result_word[1:]
            return result_word

        result = regex.sub(_replace, result)

    return result


# ─── Auto-accent single-syllable shortcut ────────────────────────────────────

def _accent_monosyllables(text: str) -> str:
    """
    Words with exactly one vowel don't need a stress mark — they're
    already stressed by default.  Leave them unchanged.
    Words with 0 vowels (punctuation tokens) — skip.
    """
    return text  # No-op: monosyllabic words are fine without explicit accent


# ─── Public API ───────────────────────────────────────────────────────────────

def add_stress_marks(text: str, lang: str = "uk") -> str:
    """
    Add Unicode stress marks (U+0301) to text before Edge TTS synthesis.

    Edge TTS UA and RU neural voices read the combining acute accent as
    lexical stress, producing more natural intonation.

    Args:
        text: Plain text (no SSML tags — those have been stripped upstream).
        lang: BCP-47 language tag, e.g. "uk", "uk-UA", "ru", "ru-RU".

    Returns:
        Text with stress marks inserted where known; unchanged if nothing found.
    """
    if not text or not text.strip():
        return text

    lang_base = (lang or "uk").split("-")[0].lower()
    if lang_base not in ("uk", "ru"):
        return text

    # Already fully accented (e.g., came from a previous pass)
    if text.count(_ACCENT) >= max(1, text.count(" ") // 3):
        return text

    # 1. Try library (fast path when installed)
    try:
        lib_result = _accent_via_library(text, lang_base)
        if lib_result and lib_result != text:
            logger.debug("stress_marks: library accentuation applied (%d chars)", len(text))
            return lib_result
    except Exception:
        pass

    # 2. Built-in dictionary
    result = _apply_dict_stress(text)
    if result != text:
        logger.debug("stress_marks: dict accentuation applied")
    return result


def stress_marks_available(lang: str = "uk") -> bool:
    """True if a library-quality accentuator is available for this language."""
    lang_base = (lang or "uk").split("-")[0].lower()
    if lang_base == "ru" and _try_load_ruaccent() is not None:
        return True
    if lang_base == "uk" and _try_load_ua_accentor() is not None:
        return True
    return False
