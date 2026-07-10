"""Obvious machine-translation pattern detection."""

from __future__ import annotations

import re
from typing import Any

# Regex patterns for known bad MT (uk/ru)
_BAD_MT_PATTERNS: list[tuple[str, str]] = [
    (r"получил\s+очаров", "ru_calque_charm"),
    (r"отримав\s+очаров", "uk_calque_charm"),
    (r"може\s+летіти", "uk_unnatural_fly"),
    (r"может\s+лететь", "ru_unnatural_fly"),
    (r"\bвін\s+відчувати,\s+що\b", "uk_bad_verb_form"),
    (r"\bв\s+відділенні\b", "uk_bad_preposition"),
    (r"\bпереможного\s+їзда\b", "uk_bad_noun_case"),
    (r"\bзі\s+смілостями\b", "uk_bad_phrase"),
    (r"\bпредставив\s+себе\s+як\b", "uk_bad_reflexive"),
    (r"будуть\s+починати\s+війн", "uk_literal_wars"),
    (r"будут\s+начинать\s+войн", "ru_literal_wars"),
    (r"был\s+применён\s+к\s+университет", "ru_calque_apply"),
    (r"був\s+застосований\s+до\s+університет", "uk_calque_apply"),
    (r"не\s+сяде", "ru_calque_sit"),
    (r"не\s+сяде\b", "uk_calque_sit"),
    (r"очаровательност", "calque_charm_noun"),
    (r"робить\s+сенс", "calque_make_sense"),
    (r"делает\s+смысл", "calque_make_sense"),
    (r"брати\s+місце", "calque_take_place"),
    (r"брать\s+место", "calque_take_place"),
    (r"Hollywoodі", "corrupted_name"),
    (r"Файат", "corrupted_brand"),
    (r"ЮСК", "corrupted_usc"),
    (r"\bмладш", "ruism_mladshiy"),
    (r"\bчто\b", "ruism_chto"),
    (r"\bэтот\b", "ruism_etot"),
    (r"\bполучил\b", "ruism_poluchil"),
    (r"\bполучила\b", "ruism_poluchil"),
]


def detect_bad_mt_patterns(text: str) -> list[dict[str, Any]]:
    t = str(text or "")
    hits: list[dict[str, Any]] = []
    for pat, code in _BAD_MT_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            hits.append({"code": code, "pattern": pat})
    return hits


def has_bad_mt(text: str) -> bool:
    return bool(detect_bad_mt_patterns(text))
