"""Language code normalization for all MT engines (language-agnostic)."""

from __future__ import annotations

LANG_ALIASES = {
    "eng": "en",
    "english": "en",
    "rus": "ru",
    "ukr": "uk",
    "ger": "de",
    "deu": "de",
    "jpn": "ja",
    "zh-cn": "zh",
    "zh_cn": "zh",
}

DEEP_LANG_MAP = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
}

NLLB_FLORES = {
    "en": "eng_Latn",
    "ru": "rus_Cyrl",
    "uk": "ukr_Cyrl",
    "de": "deu_Latn",
    "fr": "fra_Latn",
    "es": "spa_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "pl": "pol_Latn",
    "tr": "tur_Latn",
    "nl": "nld_Latn",
    "cs": "ces_Latn",
    "ro": "ron_Latn",
    "bg": "bul_Cyrl",
    "el": "ell_Grek",
    "zh": "zho_Hans",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "hi": "hin_Deva",
    "ar": "arb_Arab",
}


def normalize_lang(code: str | None) -> str:
    if not code:
        return "en"
    c = str(code).strip().lower()
    return LANG_ALIASES.get(c, c.split("-")[0])


def deep_lang(code: str) -> str:
    c = normalize_lang(code)
    return DEEP_LANG_MAP.get(c, c)


def pair_key(src: str, tgt: str) -> str:
    return f"{normalize_lang(src)}->{normalize_lang(tgt)}"


def nllb_code(code: str) -> str | None:
    return NLLB_FLORES.get(normalize_lang(code))
