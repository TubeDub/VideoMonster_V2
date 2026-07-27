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
    # Argos/word-for-word UK calques (George Lucas / general narrative)
    (r"не\s+може\s+допомогти,\s+але\s+відчувати", "uk_calque_could_not_help"),
    (r"не\s+може\s+допомогти,\s+але", "uk_calque_could_not_help_short"),
    (r"зі\s+страхом\s+очікував\s+насправді\s+отримати", "uk_calque_dreading"),
    (r"не\s+мав\s+нічого,\s+що\s+серйозно", "uk_calque_nothing_seriously"),
    (r"не\s+отримав\s+сина\s+обсесії", "uk_calque_obsession"),
    (r"сина\s+обсесії", "uk_calque_obsession_short"),
    (r"ми\s+отримаємо\s+вашу\s+реальну\s+роботу", "uk_calque_real_job"),
    (r"якщо\s+він\s+прийшов\s+цей\s+величезний\s+аргумент", "uk_calque_argument"),
    (r"\bпрокладався\b", "uk_calque_laying"),
    (r"розім['']ятити", "uk_calque_smashed_ti"),
    (r"розім['']яти", "uk_calque_smashed"),
    (r"був\s+пережили", "uk_calque_survived"),
    (r"кінематографітек", "uk_calque_cinematography"),
    (r"найземніш", "uk_calque_groundbreaking"),
    (r"не\s+довгати", "uk_calque_sure_enough"),
    (r"долі\s+наради", "uk_calque_fateful_meeting"),
    (r"виграшного\s+приводу", "uk_calque_winning_drive"),
    (r"\bлюдей\s+(?:у|в)\s+США\b", "uk_usc_as_usa"),
    (r"\bДжордж-молодший\s+який\b", "uk_missing_comma_rel"),
    (r"пішов\s+над\s+подіум", "uk_calque_walked_over"),
    (r"\d+-річному\s+хлопчику", "uk_bad_dative_subject"),
    (r"хлопчику\s+ім\.", "uk_bad_name_abbrev"),
    (r"був\s+своєрідним\s+правом", "uk_calque_kind_of_right"),
    (r"найближчий\s+досвід", "uk_calque_near_death"),
    (r"за\s+цією\s+точкою", "uk_calque_by_this_point"),
    (r"повністю\s+змінити\s+кіно", "uk_bad_infinitive_tense"),
    # Residuals that survived partial naturalize / Fast QA lock
    (r"на\s+ім['']я\s+Джорджа-молодшого", "uk_bad_name_case_after_named"),
    (r"автомобіль,\s*яка", "uk_bad_gender_agreement"),
    (r"був\s+(?:повністю\s+)?одужав", "uk_bad_double_past"),
    (r"правий\s+мав\s+рацію", "uk_bad_synonym_double"),
    (r"автомобіль\s+на\s+великій\s+швидкості\s+промчала", "uk_bad_gender_verb"),
    (r"З\s+того\s+часу,\s+як\s+його\s+майже\s+смертельний\s+досвід", "uk_bad_near_death_clause"),
    # Literary stiffness (English skeleton still visible after cosmetic polish)
    (r"\bякий\s+називається\b", "uk_stiff_which_is_called"),
    (r"\bдійсно\s+майже\s+нічого\s+серйозно\b", "uk_stiff_nothing_seriously"),
    (r"\bвідомий\s+сьогодні,?\s*як\b", "uk_stiff_known_today_as"),
    (r"\bвзяти\s+деякі\s+фотографії\b", "uk_stiff_take_some_photos"),
    (r"\bпросто\s+не\s+розумів\s+одержимості\s+сина\b", "uk_stiff_obsession"),
    # zh→uk collapsed / agreement garbage that previously got Fast QA PASS
    (r"поколінь\s+прості", "uk_bad_agreement_pokolin"),
    (r"вісім\s+поколінь\s+прості", "uk_nonsense_eight_generations"),
    (r"ви\s+товсті,\s*ви\s+вагітні", "uk_nonsense_fat_pregnant"),
    (r"ви\s+вагітні,\s*ви\s+товсті", "uk_nonsense_pregnant_fat"),
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
