"""
Named Entity Phonetic Resolver — Stage before TTS.

Translates/transliterates entity surface forms so TTS pronounces them correctly.
Examples:
  "Fiat" (uk TTS) → "Фіат"   (not "Фает")
  "BMW"  (uk TTS) → "Бі Ем Ве"
  "USC"  (uk TTS) → "Ю Ес Сі"
  "George Lucas"  → "Джордж Лукас"
"""

from __future__ import annotations

import re
from typing import Any

# ── Brand/car phonetics ────────────────────────────────────────────────────────
# { english_form: { lang: tts_form } }
# lang keys: "uk", "ru", "de", "en" (for en, keep original)

_CAR_PHONETICS: dict[str, dict[str, str]] = {
    "Fiat":        {"uk": "Фіат",       "ru": "Фиат",      "de": "Fiat"},
    "FIAT":        {"uk": "Фіат",       "ru": "Фиат",      "de": "Fiat"},
    "BMW":         {"uk": "Бі Ем Ве",   "ru": "Бэ Эм Ве",  "de": "Bé Em Vé"},
    "Mercedes":    {"uk": "Мерседес",   "ru": "Мерседес",  "de": "Mercedes"},
    "Volkswagen":  {"uk": "Фольксваген","ru": "Фольксваген","de": "Volkswagen"},
    "VW":          {"uk": "Фе Ве",      "ru": "Фэ Вэ",     "de": "Vau Vau"},
    "Audi":        {"uk": "Ауді",       "ru": "Ауди",      "de": "Audi"},
    "Toyota":      {"uk": "Тойота",     "ru": "Тойота",    "de": "Toyota"},
    "Honda":       {"uk": "Хонда",      "ru": "Хонда",     "de": "Honda"},
    "Ferrari":     {"uk": "Ферарі",     "ru": "Феррари",   "de": "Ferrari"},
    "Lamborghini": {"uk": "Ламборгіні", "ru": "Ламборгини","de": "Lamborghini"},
    "Porsche":     {"uk": "Порше",      "ru": "Порше",     "de": "Porsche"},
    "Chevrolet":   {"uk": "Шевроле",    "ru": "Шевроле",   "de": "Chevrolet"},
    "Renault":     {"uk": "Рено",       "ru": "Рено",      "de": "Renault"},
    "Peugeot":     {"uk": "Пежо",       "ru": "Пежо",      "de": "Peugeot"},
    "Citroën":     {"uk": "Сітроен",    "ru": "Ситроен",   "de": "Citroën"},
    "Citroen":     {"uk": "Сітроен",    "ru": "Ситроен",   "de": "Citroën"},
    "Hyundai":     {"uk": "Хюндай",     "ru": "Хёндэ",     "de": "Hyundai"},
    "Kia":         {"uk": "Кіа",        "ru": "Киа",       "de": "Kia"},
    "Lexus":       {"uk": "Лексус",     "ru": "Лексус",    "de": "Lexus"},
    "Tesla":       {"uk": "Тесла",      "ru": "Тесла",     "de": "Tesla"},
    "Mazda":       {"uk": "Мазда",      "ru": "Мазда",     "de": "Mazda"},
    "Subaru":      {"uk": "Субару",     "ru": "Субару",    "de": "Subaru"},
    "Mitsubishi":  {"uk": "Міцубісі",   "ru": "Мицубиси",  "de": "Mitsubishi"},
    "Nissan":      {"uk": "Ніссан",     "ru": "Ниссан",    "de": "Nissan"},
    "Ford":        {"uk": "Форд",       "ru": "Форд",      "de": "Ford"},
    "Dodge":       {"uk": "Додж",       "ru": "Додж",      "de": "Dodge"},
    "Jeep":        {"uk": "Джип",       "ru": "Джип",      "de": "Jeep"},
    "Volvo":       {"uk": "Вольво",     "ru": "Вольво",    "de": "Volvo"},
    "Opel":        {"uk": "Опель",      "ru": "Опель",     "de": "Opel"},
    "Alfa Romeo":  {"uk": "Альфа Ромео","ru": "Альфа Ромео","de": "Alfa Romeo"},
    "Range Rover": {"uk": "Рейндж Ровер","ru": "Рейндж Ровер","de": "Range Rover"},
    "Land Rover":  {"uk": "Ленд Ровер", "ru": "Ленд Ровер","de": "Land Rover"},
}

_TECH_PHONETICS: dict[str, dict[str, str]] = {
    "Apple":     {"uk": "Eppл",       "ru": "Эппл",     "de": "Apple"},
    "Google":    {"uk": "Гугл",       "ru": "Гугл",     "de": "Google"},
    "Microsoft": {"uk": "Майкрософт", "ru": "Майкрософт","de": "Microsoft"},
    "YouTube":   {"uk": "Ютьюб",      "ru": "Ютуб",     "de": "YouTube"},
    "Netflix":   {"uk": "Нетфлікс",   "ru": "Нетфликс", "de": "Netflix"},
    "Instagram": {"uk": "Інстаграм",  "ru": "Инстаграм","de": "Instagram"},
    "Twitter":   {"uk": "Твіттер",    "ru": "Твиттер",  "de": "Twitter"},
    "Facebook":  {"uk": "Фейсбук",    "ru": "Фейсбук",  "de": "Facebook"},
    "TikTok":    {"uk": "Тікток",     "ru": "Тикток",   "de": "TikTok"},
    "Spotify":   {"uk": "Спотіфай",   "ru": "Спотифай", "de": "Spotify"},
    "Telegram":  {"uk": "Телеграм",   "ru": "Телеграм", "de": "Telegram"},
    "WhatsApp":  {"uk": "Вотсап",     "ru": "Ватсап",   "de": "WhatsApp"},
    "Reddit":    {"uk": "Реддіт",     "ru": "Реддит",   "de": "Reddit"},
    "GitHub":    {"uk": "Гітхаб",     "ru": "Гитхаб",   "de": "GitHub"},
    "OpenAI":    {"uk": "Оупен Ей Ай","ru": "Опен Эй Ай","de": "OpenAI"},
    "ChatGPT":   {"uk": "Чат Джі Пі Ті","ru": "Чат Джи Пи Ти","de": "ChatGPT"},
    "Samsung":   {"uk": "Самсунг",    "ru": "Самсунг",  "de": "Samsung"},
    "Adobe":     {"uk": "Адобі",      "ru": "Адоби",    "de": "Adobe"},
    "Nvidia":    {"uk": "Енвідія",    "ru": "Энвидиа",  "de": "Nvidia"},
    "Intel":     {"uk": "Інтел",      "ru": "Интел",    "de": "Intel"},
    "Amazon":    {"uk": "Амазон",     "ru": "Амазон",   "de": "Amazon"},
}

# ── Acronym expansion ──────────────────────────────────────────────────────────
# All-caps words → spelled out letter by letter
_ACRONYM_RE = re.compile(r'\b([A-Z]{2,6})\b')

_LETTER_SOUNDS: dict[str, dict[str, str]] = {
    "uk": {
        "A": "Ей", "B": "Бі", "C": "Сі", "D": "Ді", "E": "І",
        "F": "Еф", "G": "Джі", "H": "Ейч", "I": "Ай", "J": "Джей",
        "K": "Кей", "L": "Ел", "M": "Ем", "N": "Ен", "O": "Оу",
        "P": "Пі", "Q": "К'ю", "R": "Ар", "S": "Ес", "T": "Ті",
        "U": "Ю", "V": "Ві", "W": "Дабл'ю", "X": "Екс", "Y": "Вай",
        "Z": "Зет",
    },
    "ru": {
        "A": "Эй", "B": "Бэ", "C": "Си", "D": "Ди", "E": "И",
        "F": "Эф", "G": "Джи", "H": "Эйч", "I": "Ай", "J": "Джей",
        "K": "Кей", "L": "Эл", "M": "Эм", "N": "Эн", "O": "Оу",
        "P": "Пи", "Q": "Кью", "R": "Ар", "S": "Эс", "T": "Ти",
        "U": "Ю", "V": "Ви", "W": "Дабл-ю", "X": "Экс", "Y": "Вай",
        "Z": "Зет",
    },
    "de": {
        "A": "A", "B": "Bé", "C": "Zé", "D": "Dé", "E": "E",
        "F": "Ef", "G": "Gé", "H": "Ha", "I": "I", "J": "Jot",
        "K": "Ka", "L": "El", "M": "Em", "N": "En", "O": "O",
        "P": "Pé", "Q": "Ku", "R": "Er", "S": "Es", "T": "Té",
        "U": "U", "V": "Vau", "W": "Vé", "X": "Iks", "Y": "Ypsilon",
        "Z": "Zet",
    },
}

# Known acronyms that should NOT be spelled out (used as words)
_WHOLE_WORD_ACRONYMS = frozenset({
    "NASA", "NATO", "FIFA", "UEFA", "UNICEF", "UNESCO", "OPEC",
    "FBI", "CIA", "NSA", "WHO", "WTO", "IMF", "IRS",
})


def _expand_acronym(acronym: str, lang: str) -> str:
    """Spell out an acronym letter by letter."""
    if acronym in _WHOLE_WORD_ACRONYMS:
        return acronym
    letters = _LETTER_SOUNDS.get(lang, {})
    if not letters:
        return acronym
    parts = [letters.get(ch, ch) for ch in acronym]
    return " ".join(parts)


# ── Person name phonetics ──────────────────────────────────────────────────────
_PERSON_PHONETICS: dict[str, dict[str, str]] = {
    "George":    {"uk": "Джордж",   "ru": "Джордж",   "de": "George"},
    "Lucas":     {"uk": "Лукас",    "ru": "Лукас",    "de": "Lucas"},
    "John":      {"uk": "Джон",     "ru": "Джон",     "de": "John"},
    "James":     {"uk": "Джеймс",   "ru": "Джеймс",   "de": "James"},
    "Robert":    {"uk": "Роберт",   "ru": "Роберт",   "de": "Robert"},
    "Michael":   {"uk": "Майкл",    "ru": "Майкл",    "de": "Michael"},
    "David":     {"uk": "Девід",    "ru": "Дэвид",    "de": "David"},
    "William":   {"uk": "Вільям",   "ru": "Уильям",   "de": "William"},
    "Richard":   {"uk": "Річард",   "ru": "Ричард",   "de": "Richard"},
    "Daniel":    {"uk": "Деніел",   "ru": "Дэниэл",   "de": "Daniel"},
    "Matthew":   {"uk": "Метью",    "ru": "Мэтью",    "de": "Matthew"},
    "Andrew":    {"uk": "Ендрю",    "ru": "Эндрю",    "de": "Andrew"},
    "Kevin":     {"uk": "Кевін",    "ru": "Кевин",    "de": "Kevin"},
    "Steven":    {"uk": "Стівен",   "ru": "Стивен",   "de": "Steven"},
    "Stephen":   {"uk": "Стівен",   "ru": "Стивен",   "de": "Stephen"},
    "Thomas":    {"uk": "Томас",    "ru": "Томас",    "de": "Thomas"},
    "Charles":   {"uk": "Чарльз",   "ru": "Чарльз",   "de": "Charles"},
    "Edward":    {"uk": "Едвард",   "ru": "Эдвард",   "de": "Edward"},
    "Brian":     {"uk": "Браян",    "ru": "Брайан",   "de": "Brian"},
    "Ryan":      {"uk": "Райан",    "ru": "Райан",    "de": "Ryan"},
    "Tyler":     {"uk": "Тайлер",   "ru": "Тайлер",   "de": "Tyler"},
    "Brandon":   {"uk": "Брендон",  "ru": "Брэндон",  "de": "Brandon"},
    "Justin":    {"uk": "Джастін",  "ru": "Джастин",  "de": "Justin"},
    "Nathan":    {"uk": "Нейтан",   "ru": "Нейтан",   "de": "Nathan"},
    "Patrick":   {"uk": "Патрік",   "ru": "Патрик",   "de": "Patrick"},
    "Jason":     {"uk": "Джейсон",  "ru": "Джейсон",  "de": "Jason"},
    "Jr":        {"uk": "Молодший", "ru": "Младший",  "de": "Junior"},
    "Junior":    {"uk": "Молодший", "ru": "Младший",  "de": "Junior"},
    "Sr":        {"uk": "Старший",  "ru": "Старший",  "de": "Senior"},
    "Senior":    {"uk": "Старший",  "ru": "Старший",  "de": "Senior"},
    # Common last names
    "Smith":     {"uk": "Сміт",     "ru": "Смит",     "de": "Smith"},
    "Johnson":   {"uk": "Джонсон",  "ru": "Джонсон",  "de": "Johnson"},
    "Williams":  {"uk": "Вільямс",  "ru": "Уильямс",  "de": "Williams"},
    "Brown":     {"uk": "Браун",    "ru": "Браун",    "de": "Brown"},
    "Jones":     {"uk": "Джонс",    "ru": "Джонс",    "de": "Jones"},
    "Miller":    {"uk": "Міллер",   "ru": "Миллер",   "de": "Miller"},
    "Davis":     {"uk": "Дейвіс",   "ru": "Дэвис",    "de": "Davis"},
    "Wilson":    {"uk": "Вілсон",   "ru": "Уилсон",   "de": "Wilson"},
    "Moore":     {"uk": "Мур",      "ru": "Мур",      "de": "Moore"},
    "Taylor":    {"uk": "Тейлор",   "ru": "Тейлор",   "de": "Taylor"},
    "Anderson":  {"uk": "Андерсон", "ru": "Андерсон", "de": "Anderson"},
    "Martin":    {"uk": "Мартін",   "ru": "Мартин",   "de": "Martin"},
    "Cooper":    {"uk": "Купер",    "ru": "Купер",    "de": "Cooper"},
    "Walker":    {"uk": "Вокер",    "ru": "Уокер",    "de": "Walker"},
    "Harris":    {"uk": "Гарріс",   "ru": "Харрис",   "de": "Harris"},
    "Jackson":   {"uk": "Джексон",  "ru": "Джексон",  "de": "Jackson"},
    "Thompson":  {"uk": "Томпсон",  "ru": "Томпсон",  "de": "Thompson"},
    "White":     {"uk": "Вайт",     "ru": "Уайт",     "de": "White"},
    "Lee":       {"uk": "Лі",       "ru": "Ли",       "de": "Lee"},
    "Clark":     {"uk": "Кларк",    "ru": "Кларк",    "de": "Clark"},
    "Hall":      {"uk": "Гол",      "ru": "Холл",     "de": "Hall"},
    "Lewis":     {"uk": "Льюїс",    "ru": "Льюис",    "de": "Lewis"},
    "Scott":     {"uk": "Скотт",    "ru": "Скотт",    "de": "Scott"},
    "Parker":    {"uk": "Паркер",   "ru": "Паркер",   "de": "Parker"},
    "Ford":      {"uk": "Форд",     "ru": "Форд",     "de": "Ford"},
    "Young":     {"uk": "Янг",      "ru": "Янг",      "de": "Young"},
    "King":      {"uk": "Кінг",     "ru": "Кинг",     "de": "King"},
    "Wright":    {"uk": "Райт",     "ru": "Райт",     "de": "Wright"},
    "Evans":     {"uk": "Еванс",    "ru": "Эванс",    "de": "Evans"},
    "Collins":   {"uk": "Коллінз",  "ru": "Коллинз",  "de": "Collins"},
    "Stewart":   {"uk": "Стьюарт",  "ru": "Стюарт",   "de": "Stewart"},
    "Carter":    {"uk": "Картер",   "ru": "Картер",   "de": "Carter"},
    "Murphy":    {"uk": "Мерфі",    "ru": "Мёрфи",    "de": "Murphy"},
    "Roberts":   {"uk": "Робертс",  "ru": "Робертс",  "de": "Roberts"},
    "Baker":     {"uk": "Бейкер",   "ru": "Бейкер",   "de": "Baker"},
    "Reed":      {"uk": "Рід",      "ru": "Рид",      "de": "Reed"},
    "Rivera":    {"uk": "Рівера",   "ru": "Ривера",   "de": "Rivera"},
    "Perry":     {"uk": "Перрі",    "ru": "Перри",    "de": "Perry"},
    "Torres":    {"uk": "Торрес",   "ru": "Торрес",   "de": "Torres"},
    "Hill":      {"uk": "Гілл",     "ru": "Хилл",     "de": "Hill"},
    "Flores":    {"uk": "Флорес",   "ru": "Флорес",   "de": "Flores"},
    "Ross":      {"uk": "Росс",     "ru": "Росс",     "de": "Ross"},
    "Howard":    {"uk": "Говард",   "ru": "Говард",   "de": "Howard"},
    "Sanders":   {"uk": "Сандерс",  "ru": "Сандерс",  "de": "Sanders"},
    "Price":     {"uk": "Прайс",    "ru": "Прайс",    "de": "Price"},
    "Bell":      {"uk": "Белл",     "ru": "Белл",     "de": "Bell"},
    "Coleman":   {"uk": "Коулман",  "ru": "Коулман",  "de": "Coleman"},
    "Butler":    {"uk": "Батлер",   "ru": "Батлер",   "de": "Butler"},
    "Henderson": {"uk": "Хендерсон","ru": "Хендерсон","de": "Henderson"},
    "Barnes":    {"uk": "Барнс",    "ru": "Барнс",    "de": "Barnes"},
    "Fisher":    {"uk": "Фішер",    "ru": "Фишер",    "de": "Fisher"},
    "Chapman":   {"uk": "Чепман",   "ru": "Чепман",   "de": "Chapman"},
    "Spencer":   {"uk": "Спенсер",  "ru": "Спенсер",  "de": "Spencer"},
}

# ── University / org phonetics ─────────────────────────────────────────────────
_ORG_PHONETICS: dict[str, dict[str, str]] = {
    "USC":      {"uk": "Ю Ес Сі",    "ru": "Ю Эс Си",   "de": "U Es Tsé"},
    "UCLA":     {"uk": "Ю Сі Ел Ей", "ru": "Ю Си Эл Эй","de": "U Tsé El A"},
    "MIT":      {"uk": "Ем Ай Ті",   "ru": "Эм Ай Ти",  "de": "Em I Té"},
    "NASA":     {"uk": "Наса",       "ru": "НАСА",       "de": "NASA"},
    "NATO":     {"uk": "НАТО",       "ru": "НАТО",       "de": "NATO"},
    "FBI":      {"uk": "Еф Бі Ай",   "ru": "Эф Би Ай",  "de": "Ef Bé I"},
    "CIA":      {"uk": "Сі Ай Ей",   "ru": "Си Ай Эй",  "de": "Tsé I A"},
    "NFL":      {"uk": "Ен Еф Ел",   "ru": "Эн Эф Эл",  "de": "En Ef El"},
    "NBA":      {"uk": "Ен Бі Ей",   "ru": "Эн Би Эй",  "de": "En Bé A"},
}


def _make_lookup(
    tables: list[dict[str, dict[str, str]]]
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for t in tables:
        for k, v in t.items():
            out[k] = v
            out[k.upper()] = v
            out[k.lower()] = {lang: val.lower() for lang, val in v.items()}
    return out


_ALL_PHONETICS = _make_lookup([
    _CAR_PHONETICS, _TECH_PHONETICS, _PERSON_PHONETICS, _ORG_PHONETICS
])


def resolve_phonetics(text: str, lang: str) -> tuple[str, list[str]]:
    """
    Replace entity surface forms with TTS-friendly phonetic equivalents.

    Returns (resolved_text, list_of_changes).
    Only called for Cyrillic-target languages (uk/ru/de) where foreign
    proper nouns may be mispronounced.
    """
    base = (lang or "en").split("-")[0].lower()
    if base == "en":
        return text, []  # English TTS handles English names fine

    changes: list[str] = []
    result = text

    # 1. Named phonetic replacements (longest match first)
    for english_form, translations in sorted(
        _ALL_PHONETICS.items(), key=lambda x: -len(x[0])
    ):
        target = translations.get(base)
        if not target:
            continue
        # Case-sensitive word-boundary replacement
        pattern = r'\b' + re.escape(english_form) + r'\b'
        new = re.sub(pattern, target, result)
        if new != result:
            changes.append(f"{english_form}→{target}")
            result = new

    # 2. Remaining all-caps acronyms (3-5 letters, not yet replaced)
    def _replace_acronym(m: re.Match) -> str:
        acronym = m.group(1)
        expanded = _expand_acronym(acronym, base)
        if expanded != acronym:
            changes.append(f"{acronym}→{expanded}")
        return expanded

    result = _ACRONYM_RE.sub(_replace_acronym, result)

    return result, changes
