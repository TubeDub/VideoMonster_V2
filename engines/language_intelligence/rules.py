"""Static + learned rules — self-contained, no imports from other TubeDub engines."""

from __future__ import annotations

import re
from typing import Any

# Ukrainian: Russian word → Ukrainian replacement (word boundary)
UK_RUISM_RULES: list[tuple[str, str, str]] = [
    (r"\bОн\b", "Він", "ru_pronoun"),
    (r"\bон\b", "він", "ru_pronoun"),
    (r"\bОна\b", "Вона", "ru_pronoun"),
    (r"\bона\b", "вона", "ru_pronoun"),
    (r"\bОни\b", "Вони", "ru_pronoun"),
    (r"\bони\b", "вони", "ru_pronoun"),
    (r"\bего\b", "його", "ru_pronoun"),
    (r"\bЕго\b", "Його", "ru_pronoun"),
    (r"\bеё\b", "її", "ru_pronoun"),
    (r"\bЕё\b", "Її", "ru_pronoun"),
    (r"\bчто\b", "що", "ru_word"),
    (r"\bЧто\b", "Що", "ru_word"),
    (r"\bэтот\b", "цей", "ru_word"),
    (r"\bЭтот\b", "Цей", "ru_word"),
    (r"\bэта\b", "ця", "ru_word"),
    (r"\bэто\b", "це", "ru_word"),
    (r"\bещё\b", "ще", "ru_word"),
    (r"\bеще\b", "ще", "ru_word"),
    (r"\bЕщё\b", "Ще", "ru_word"),
    (r"\bчтобы\b", "щоб", "ru_word"),
    (r"\bЧтобы\b", "Щоб", "ru_word"),
    (r"\bно\b", "але", "ru_word"),
    (r"\bНо\b", "Але", "ru_word"),
    (r"\bМладший\b", "молодший", "ru_word"),
    (r"\bмладший\b", "молодший", "ru_word"),
    (r"\bкоторый\b", "який", "ru_word"),
    (r"\bкоторая\b", "яка", "ru_word"),
    (r"\bкоторые\b", "які", "ru_word"),
    (r"\bсейчас\b", "зараз", "ru_word"),
    (r"\bСейчас\b", "Зараз", "ru_word"),
    (r"\bочень\b", "дуже", "ru_word"),
    (r"\bтоже\b", "теж", "ru_word"),
    (r"\bтакже\b", "також", "ru_word"),
    (r"\bесли\b", "якщо", "ru_word"),
    (r"\bкогда\b", "коли", "ru_word"),
    (r"\bнет\b", "немає", "ru_word"),
    (r"\bНет\b", "Немає", "ru_word"),
]

# Calques EN→UK
UK_CALQUE_RULES: list[tuple[str, str, str]] = [
    (r"\bробить\s+сенс\b", "має сенс", "calque"),
    (r"\bробити\s+сенс\b", "мати сенс", "calque"),
    (r"\bбрати\s+місце\b", "відбувається", "calque"),
    (r"\bбере\s+місце\b", "відбувається", "calque"),
    (r"\bмати\s+місце\s+бути\b", "відбувається", "calque"),
    (r"\bшматок\s+торта\b", "легко", "calque"),
    (r"\bце\s+повертається\b", "виявляється", "calque"),
    (r"\bна\s+даний\s+момент\s+часу\b", "зараз", "calque"),
    (r"\bз\s+іншого\s+боку\s+руки\b", "з іншого боку", "calque"),
]

# Brands / titles — applied when source contains Latin token
KEEP_LATIN: list[str] = [
    "Fiat", "USC", "U.S.C.", "Hollywood", "Lucasfilm", "Disney", "Netflix",
    "YouTube", "Google", "NASA", "MIT", "iPhone", "ChatGPT",
]

PREFERRED_UA_TITLES: dict[str, str] = {
    "Star Wars": "Зоряні війни",
}

CYRILLIC_MISTRANSLATIONS: dict[str, list[str]] = {
    "Fiat": ["Фіат", "Фиат", "Фiat", "фіат"],
    "USC": ["ЮСК", "Ю.С.К.", "юск"],
    "Star Wars": ["Стар Wars", "Стар Вars", "Звёздные войны"],
}

TRANSLITERATE_NAMES: dict[str, str] = {
    "George Lucas": "Джордж Лукас",
    "James Cameron": "Джеймс Кемерон",
    "Steven Spielberg": "Стівен Спілберг",
}


def detect_russian_words(text: str, tgt_lang: str) -> list[str]:
    if tgt_lang.split("-")[0] != "uk":
        return []
    words = re.findall(r"\b[\w'-]+\b", str(text or ""), flags=re.UNICODE)
    hits = []
    ru_set = {
        "что", "этот", "эта", "это", "они", "его", "её", "ее", "нет", "чтобы",
        "ещё", "еще", "который", "которая", "которые", "сейчас", "очень",
        "тоже", "также", "если", "когда", "но", "младший", "он", "она",
    }
    for w in words:
        if w.lower() in ru_set:
            hits.append(w)
    return hits


def detect_english_leak(text: str, original: str, tgt_lang: str) -> list[str]:
    if tgt_lang.split("-")[0] not in ("uk", "ru"):
        return []
    orig_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{2,}\b", original or "")}
    keep = {k.lower() for k in KEEP_LATIN}
    keep.update(k.lower() for k in PREFERRED_UA_TITLES)
    keep.update(k.lower() for k in TRANSLITERATE_NAMES)
    tr_words = re.findall(r"\b[a-zA-Z]{2,}\b", text or "")
    return [w for w in tr_words if w.lower() in orig_words and w.lower() not in keep]


def load_learned_rules(memory_rules: list[dict[str, Any]]) -> list[tuple[str, str, str, float]]:
    """Permanent learned rules: (pattern, replacement, category, confidence)."""
    out: list[tuple[str, str, str, float]] = []
    for r in memory_rules:
        if not r.get("permanent"):
            continue
        pat = str(r.get("pattern") or "")
        repl = str(r.get("replacement") or "")
        if pat and repl:
            out.append((pat, repl, str(r.get("category") or "learned"), float(r.get("confidence") or 0.9)))
    return sorted(out, key=lambda x: -x[3])


def static_rules_for_lang(tgt_lang: str) -> list[tuple[str, str, str, float]]:
    base = tgt_lang.split("-")[0]
    rules: list[tuple[str, str, str, float]] = []
    if base == "uk":
        for pat, repl, cat in UK_RUISM_RULES + UK_CALQUE_RULES:
            rules.append((pat, repl, cat, 0.99 if cat != "calque" else 0.92))
    return rules


def all_fix_rules(tgt_lang: str, learned: list[dict[str, Any]]) -> list[tuple[str, str, str, float]]:
    return static_rules_for_lang(tgt_lang) + load_learned_rules(learned)
