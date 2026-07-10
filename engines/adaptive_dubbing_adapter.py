"""
Adaptive Dubbing Adapter — TubeDub 1.0

Sits between Natural Translation and TTS.
This is the ONLY place where text may be changed before synthesis.

Decision tree (strictly in order, early exit when text fits):
  1. Duration predict → if fits, pass directly to TTS
  2. Reframe         → restructure sentence (passive→active, fronted clause→end)
  3. Synonyms        → replace long words with shorter natural equivalents
  4. Word order      → move heavy fronted adverbials to the end
  5. Remove secondary→ drop ONLY fillers / intro phrases / redundant connectors
                       PROTECTED: names, actions, key nouns, negations, numbers, dates

Pre-TTS validation (always):
  - no technical tokens / broken markup
  - meaning preserved (word retention ≥ MIN_WORD_RETENTION)
  - predicted duration fits within slot tolerance
  - no repetitions / duplicate words

Natural pause injection:
  - calculates the appropriate post-sentence pause (80-220 ms)
    based on ending punctuation
  - stored in `AdaptResult.natural_pause_ms`
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.engines.adaptive_dubbing_adapter")

# ─── Timing tolerance ─────────────────────────────────────────────────────────
# PASS threshold: ratio < 1.15 → text fits, NO adaptation allowed.
# REPHRASE threshold: 1.15 ≤ ratio < 1.25 → only safe restructuring.
# REDUCTION threshold: ratio ≥ 1.25 → secondary removal allowed.
# (Synced with engines.dubbing_engine.predictor constants)
_PASS_THRESHOLD_PCT: float = 15.0  # < +15% → PASS
_REDUCE_THRESHOLD_PCT: float = 25.0  # ≥ +25% → secondary reduction allowed
_HARD_OVERFLOW_WARN_PCT: float = 50.0  # ≥ +50% → overflow warning

# We never compress text below this fraction of the original word count.
# 0.85 = must keep 85% of semantic information as per ТЗ.
_MIN_WORD_RETENTION: float = 0.85

# Natural post-sentence pause (ms) — matches timing_fit constants
_PUNCT_PAUSE_MS: dict[str, int] = {
    ".": 160,
    "!": 150,
    "?": 150,
    "…": 200,
    ";": 100,
    ":": 90,
    ",": 80,
}
_DEFAULT_PAUSE_MS: int = 120

# ─── Protected patterns (must NEVER be removed) ───────────────────────────────
_NEGATIONS = frozenset(
    {
        # Russian
        "не",
        "нет",
        "никогда",
        "никто",
        "ничего",
        "ничто",
        "нигде",
        "никак",
        "никуда",
        "вовсе",
        "совсем",
        "отнюдь",
        # Ukrainian
        "ні",
        "не",
        "ніколи",
        "ніхто",
        "нічого",
        "ніщо",
        "ніде",
        "ніяк",
        "нікуди",
        "зовсім",
        "анітрохи",
        # English
        "not",
        "no",
        "never",
        "neither",
        "nor",
        "nobody",
        "nothing",
        "nowhere",
        "without",
    }
)

_NUMBER_RE = re.compile(r"\b\d[\d,.]*\b")
_DATE_RE = re.compile(
    r"\b(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}\s+\w+\s+\d{4}|"
    r"\d{4}\s+(?:рік|год|year))\b",
    re.IGNORECASE,
)

# ─── Filler / introductory words (safe to remove) ─────────────────────────────
_SENTENCE_FILLERS_RU = re.compile(
    r"^(Конечно,?|Безусловно,?|Видимо,?|Кажется,?|Очевидно,?|"
    r"Честно говоря,?|Признаться,?|Вообще говоря,?|"
    r"Между прочим,?|Кстати,?|Собственно,?|Так вот,?|"
    r"Как ни странно,?|Что ж,?)\s+",
    re.IGNORECASE,
)
_SENTENCE_FILLERS_UK = re.compile(
    r"^(Звісно,?|Безумовно,?|Мабуть,?|Здається,?|Очевидно,?|"
    r"Чесно кажучи,?|Зрештою,?|Загалом кажучи,?|"
    r"До речі,?|Власне,?|Ну от,?|Хай там як,?|"
    r"Як не дивно,?|Що ж,?)\s+",
    re.IGNORECASE,
)
_SENTENCE_FILLERS_EN = re.compile(
    r"^(Of course,?|Obviously,?|Clearly,?|Honestly,?|"
    r"Frankly,?|Admittedly,?|Needless to say,?|"
    r"By the way,?|Incidentally,?|As a matter of fact,?|"
    r"I must say,?|You know,?)\s+",
    re.IGNORECASE,
)

# Redundant trailing phrases
_TRAILING_FILLER_RU = re.compile(
    r",?\s+(вы понимаете|понимаете|знаете|вот|так сказать|как бы)\.?\s*$",
    re.IGNORECASE,
)
_TRAILING_FILLER_UK = re.compile(
    r",?\s+(розумієте|знаєте|от|так би мовити|ніби)\.?\s*$",
    re.IGNORECASE,
)
_TRAILING_FILLER_EN = re.compile(
    r",?\s+(you know|you see|right\?|okay\?|isn't it\?|you understand)\.?\s*$",
    re.IGNORECASE,
)

# ─── Reframe rules ────────────────────────────────────────────────────────────
# Move fronted adverbial clauses to the end of the sentence.
# Pattern: "AdverbialPhrase, MAIN_CLAUSE" → "MAIN_CLAUSE, AdverbialPhrase"
_FRONTED_CLAUSE_RU = [
    # "Несмотря на то что X, Y" → "Y, несмотря на X"
    (
        re.compile(r"^Несмотря на то,? что (.+?),\s+(.+)$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, несмотря на то что {m.group(1).strip()}",
    ),
    # "Хотя X, Y" → "Y, хотя X"
    (
        re.compile(r"^Хотя (.{5,50}),\s+(.{5,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, хотя {m.group(1).strip()}",
    ),
    # "Если X, то Y" → "Y, если X"
    (
        re.compile(r"^Если (.{5,50}),\s+то\s+(.{5,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, если {m.group(1).strip()}",
    ),
    # "Когда X, Y" → "Y, когда X" (only short X)
    (
        re.compile(r"^Когда (.{5,35}),\s+(.{10,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, когда {m.group(1).strip()}",
    ),
]

_FRONTED_CLAUSE_UK = [
    (
        re.compile(r"^Незважаючи на те,? що (.+?),\s+(.+)$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, незважаючи на те що {m.group(1).strip()}",
    ),
    (
        re.compile(r"^Хоча (.{5,50}),\s+(.{5,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, хоча {m.group(1).strip()}",
    ),
    (
        re.compile(r"^Якщо (.{5,50}),\s+то\s+(.{5,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, якщо {m.group(1).strip()}",
    ),
    (
        re.compile(r"^Коли (.{5,35}),\s+(.{10,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, коли {m.group(1).strip()}",
    ),
]

_FRONTED_CLAUSE_EN = [
    (
        re.compile(r"^Although (.{5,50}),\s+(.{5,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, although {m.group(1).strip()}",
    ),
    (
        re.compile(r"^Even though (.{5,50}),\s+(.{5,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, even though {m.group(1).strip()}",
    ),
    (
        re.compile(r"^When (.{5,35}),\s+(.{10,})$", re.IGNORECASE),
        lambda m: f"{m.group(2).strip()}, when {m.group(1).strip()}",
    ),
]

# ─── Synonym table ─────────────────────────────────────────────────────────────
# (lang_prefix or "*") → {long_word: short_word}
_SYNONYMS: dict[str, dict[str, str]] = {
    "ru": {
        "приблизительно": "примерно",
        "незамедлительно": "сразу",
        "продемонстрировать": "показать",
        "продемонстрировал": "показал",
        "продемонстрировала": "показала",
        "использовать": "применить",
        "использовал": "применил",
        "использовала": "применила",
        "осуществить": "сделать",
        "осуществлял": "делал",
        "предоставить": "дать",
        "предоставлять": "давать",
        "предоставил": "дал",
        "присутствовать": "быть",
        "присутствовал": "был",
        "свидетельствовать": "значить",
        "свидетельствует": "значит",
        "действительно": "правда",
        "необходимо": "нужно",
        "обязательно": "нужно",
        "следует": "нужно",
        "является": "—",
        "являются": "—",
        "потому что": "так как",
        "для того чтобы": "чтобы",
        "в настоящее время": "сейчас",
        "в данный момент": "сейчас",
        "несмотря на это": "но",
        "тем не менее": "но",
        "следовательно": "значит",
        "соответственно": "значит",
        "принимать": "брать",
        "принять": "взять",
        "принял": "взял",
        "очень хорошо": "отлично",
        "очень плохо": "ужасно",
        "очень быстро": "стремительно",
        "такой образом": "так",
        "таким образом": "так",
    },
    "uk": {
        "приблизно": "близько",
        "продемонструвати": "показати",
        "продемонстрував": "показав",
        "продемонструвала": "показала",
        "використовувати": "застосовувати",
        "використовував": "застосовував",
        "здійснити": "зробити",
        "здійснював": "робив",
        "надати": "дати",
        "надавати": "давати",
        "надав": "дав",
        "свідчити": "значити",
        "свідчить": "значить",
        "необхідно": "потрібно",
        "обов'язково": "потрібно",
        "слід": "треба",
        "тому що": "бо",
        "для того щоб": "щоб",
        "на даний момент": "зараз",
        "на даний час": "зараз",
        "незважаючи на це": "але",
        "тим не менш": "але",
        "таким чином": "так",
        "відповідно": "відтак",
    },
    "en": {
        "approximately": "about",
        "immediately": "at once",
        "utilize": "use",
        "demonstrate": "show",
        "subsequently": "then",
        "additionally": "also",
        "consequently": "so",
        "furthermore": "also",
        "nonetheless": "still",
        "nevertheless": "but",
        "it is important": "importantly",
        "in order to": "to",
        "due to the fact that": "because",
        "for the purpose of": "to",
        "has the ability to": "can",
        "is able to": "can",
        "make a decision": "decide",
        "come to a conclusion": "conclude",
    },
    "*": {
        # universal
    },
}


# ─── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class AdaptStep:
    name: str  # e.g. "predict", "reframe", "synonyms", ...
    applied: bool
    text_before: str
    text_after: str
    predicted_ms_before: int
    predicted_ms_after: int
    reason: str = ""


@dataclass
class AdaptResult:
    original: str
    text: str  # final adapted text for TTS
    fits: bool  # predicted duration fits in slot
    predicted_ms: int  # estimated TTS duration of final text
    slot_ms: int
    natural_pause_ms: int
    lang: str
    steps: list[AdaptStep] = field(default_factory=list)
    validation_ok: bool = True
    validation_notes: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    variant_log: list[dict] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.text != self.original


# ─── Duration predictor ───────────────────────────────────────────────────────


def predict_duration_ms(text: str, lang: str) -> int:
    """
    Estimate TTS speech duration using syllable model (NOT character count).

    Delegates to engines.dubbing_engine.predictor for consistent results
    across the whole pipeline.
    """
    try:
        from engines.dubbing_engine.predictor import predict_ms

        return predict_ms(text, lang)
    except Exception:
        # Emergency fallback: word-based estimate (100ms per word minimum)
        t = str(text or "").strip()
        if not t:
            return 0
        words = len(t.split())
        return max(words * 130, int(len(t) / 13.5 * 1000))


def _overflow_pct(text: str, lang: str, slot_ms: int) -> float:
    """Return overflow as percentage: 0 = fits, 20 = 20% over slot."""
    if slot_ms <= 0:
        return 0.0
    predicted = predict_duration_ms(text, lang)
    return max(0.0, 100.0 * (predicted - slot_ms) / slot_ms)


def fits_in_slot(text: str, lang: str, slot_ms: int) -> bool:
    """True if predicted TTS duration is within PASS threshold (ratio < 1.15)."""
    if slot_ms <= 0:
        return True
    return _overflow_pct(text, lang, slot_ms) < _PASS_THRESHOLD_PCT


# ─── Natural pause ────────────────────────────────────────────────────────────


def natural_pause_ms(text: str) -> int:
    """Return the appropriate post-sentence pause (80–220 ms) based on punctuation."""
    t = (text or "").rstrip()
    if not t:
        return _DEFAULT_PAUSE_MS
    last = t[-1]
    return max(80, min(220, _PUNCT_PAUSE_MS.get(last, _DEFAULT_PAUSE_MS)))


# ─── Protected-word guard ─────────────────────────────────────────────────────


def _contains_protected_word(word: str, source_hint: str) -> bool:
    """Return True if this word must not be removed."""
    w = word.strip(".,!?;:—–-")
    if not w:
        return False
    # Numbers / dates
    if _NUMBER_RE.fullmatch(w):
        return True
    # Negations
    if w.lower() in _NEGATIONS:
        return True
    # Proper names (capitalized, present in source)
    if w[0].isupper() and len(w) > 1:
        if source_hint and w.lower() in source_hint.lower():
            return True
    return False


# ─── Step 2: Reframe — single-best (legacy, kept for backward compat) ─────────


def _step_reframe(text: str, lang: str) -> str | None:
    """
    Single-best fronted-clause reframe (legacy path).
    The main rephrase path now uses _generate_rephrase_variants + _select_best_rephrase.
    """
    base = (lang or "en").split("-")[0].lower()
    rules = (
        _FRONTED_CLAUSE_RU
        if base == "ru"
        else _FRONTED_CLAUSE_UK if base == "uk" else _FRONTED_CLAUSE_EN
    )
    for pattern, transform in rules:
        m = pattern.match(text.strip())
        if m:
            candidate = transform(m)
            candidate = " ".join(candidate.split())
            if candidate and candidate != text:
                return candidate
    return None


# ─── Multi-Variant Structural Rephrase ────────────────────────────────────────
# 13+ rule-based patterns that structurally rewrite sentences.
# Each pattern targets a different linguistic construct.

# 1. Trailing gerund/participial phrase removal
# UK: -ючи/-ячи/-ачи/-учи/-вши/-ши
# RU: -я/-а/-вши/-ши/-учи/-ючи
_TRAILING_GERUND_UK = re.compile(
    r",\s+(?:не\s+)?[а-яіїєА-ЯІЇЄ][а-яіїє]+"
    r"(?:ючи|ячи|ачи|учи|ючись|ачись|вши|ши)"
    r"(?:\s+[\w\'\-]+){0,8}[\.!?]?\s*$",
    re.IGNORECASE | re.UNICODE,
)
_TRAILING_GERUND_RU = re.compile(
    r",\s+(?:не\s+)?[а-яёА-ЯЁ][а-яё]+"
    r"(?:ая|яя|уясь|ючи|вшись|вши|ши|ясь|ась)"
    r"(?:\s+[\w\'\-]+){0,8}[\.!?]?\s*$",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_remove_trailing_gerund(text: str, lang: str) -> str | None:
    """
    'Він їхав містом, повертаючись додому на обід.' → 'Він їхав містом.'
    'Он ехал домой, думая о работе.' → 'Он ехал домой.'
    """
    base = (lang or "uk").split("-")[0].lower()
    pat = (
        _TRAILING_GERUND_UK
        if base == "uk"
        else (_TRAILING_GERUND_RU if base == "ru" else None)
    )
    if pat is None:
        return None
    stripped = text.rstrip()
    end_p = stripped[-1] if stripped and stripped[-1] in ".!?" else "."
    result = pat.sub(end_p, stripped).strip()
    if result and result != text.strip() and len(result.split()) >= 3:
        return result
    return None


# 2. Apposition compression: "NOUN на ім'я/по имени NAME" → "NAME"
_APPOSITION_UK = re.compile(
    r"(\d{1,3}[\-–]\w+\s+)?"
    r"(?:хлопець|юнак|дитина|дівчина|чоловік|жінка|людина|підліток|студент|учень|парубок)"
    r"\s+на\s+[іi]м['ʼ]?я\s+"
    r"([А-ЯІЇЄ][А-ЯІЇЄа-яіїє\-]+(?:[\s\-][А-ЯІЇЄ][А-ЯІЇЄа-яіїє\-]+)?)",
    re.IGNORECASE | re.UNICODE,
)
_APPOSITION_RU = re.compile(
    r"(\d{1,3}[\-–]\w+\s+)?"
    r"(?:парень|юноша|ребёнок|девушка|мужчина|женщина|человек|подросток|студент|ученик)"
    r"\s+по\s+имени\s+"
    r"([А-ЯЁA-Z][А-ЯЁа-яёA-Za-z\-]+(?:[\s\-][А-ЯЁA-Z][А-ЯЁа-яёA-Za-z\-]+)?)",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_compress_apposition(text: str, lang: str) -> str | None:
    """
    '18-річний хлопець на ім'я Джордж-молодший їхав…'
    → '18-річний Джордж-молодший їхав…'
    """
    base = (lang or "uk").split("-")[0].lower()
    pat = _APPOSITION_UK if base == "uk" else (_APPOSITION_RU if base == "ru" else None)
    if pat is None:
        return None

    def _repl(m: re.Match) -> str:
        age = (m.group(1) or "").strip()
        name = m.group(2).strip()
        return f"{age} {name}".strip() if age else name

    result = pat.sub(_repl, text, count=1).strip()
    if result and result != text.strip() and len(result.split()) >= 3:
        return " ".join(result.split())
    return None


# 3. Location phrase compression: "через своє рідне місто" → "містом"
_LOCATION_THROUGH_UK = re.compile(
    r"через\s+(?:своє?\s+|ріdn\w*\s+|рідне?\s+|старе?\s+|велике?\s+)?(?:рідне?\s+)?"
    r"(місто|містечко|селище|район|вулиц\w+|квартал)",
    re.IGNORECASE | re.UNICODE,
)
_LOCATION_REPL_UK: dict[str, str] = {
    "місто": "містом",
    "містечко": "містечком",
    "селище": "селищем",
    "район": "районом",
}

_LOCATION_THROUGH_RU = re.compile(
    r"через\s+(?:свой?\s+|родной?\s+|старый?\s+|большой?\s+)?"
    r"(город|городок|посёлок|район|улиц\w+|квартал)",
    re.IGNORECASE | re.UNICODE,
)
_LOCATION_REPL_RU: dict[str, str] = {
    "город": "по городу",
    "городок": "по городку",
    "посёлок": "по посёлку",
    "район": "по району",
}


def _rephrase_compress_location(text: str, lang: str) -> str | None:
    """'їхав через своє рідне місто' → 'їхав містом'."""
    base = (lang or "uk").split("-")[0].lower()
    if base == "uk":
        m = _LOCATION_THROUGH_UK.search(text)
        if m:
            noun = m.group(1).lower().rstrip("уюоа")
            repl = _LOCATION_REPL_UK.get(noun, noun + "ом")
            result = _LOCATION_THROUGH_UK.sub(repl, text, count=1).strip()
            if result != text.strip():
                return " ".join(result.split())
    elif base == "ru":
        m = _LOCATION_THROUGH_RU.search(text)
        if m:
            noun = m.group(1).lower()
            repl = _LOCATION_REPL_RU.get(noun, f"по {noun}у")
            result = _LOCATION_THROUGH_RU.sub(repl, text, count=1).strip()
            if result != text.strip():
                return " ".join(result.split())
    return None


# 4. Relative clause compression: ",який/яка ... V" → ",що V"
_RELATIVE_LONG_UK = re.compile(
    r",\s+(?:який|яка|яке|які)\s+"
    r"(?:в\s+цей\s+момент\s+|нещодавно\s+|щойно\s+|вже\s+|наразі\s+|на\s+даний\s+час\s+)?"
    r"([а-яіїєА-ЯІЇЄ][а-яіїє\s\w]{3,35}?)"
    r"(?=,|\.|$)",
    re.IGNORECASE | re.UNICODE,
)
_RELATIVE_LONG_RU = re.compile(
    r",\s+(?:который|которая|которое|которые)\s+"
    r"(?:в\s+этот\s+момент\s+|недавно\s+|только\s+что\s+|уже\s+|сейчас\s+)?"
    r"([а-яёА-ЯЁ][а-яё\s\w]{3,35}?)"
    r"(?=,|\.|$)",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_compress_relative(text: str, lang: str) -> str | None:
    """
    'Джордж, який в цей момент одужував від травм, стояв'
    → 'Джордж, що одужував від травм, стояв'
    """
    base = (lang or "uk").split("-")[0].lower()
    if base == "uk":
        result = _RELATIVE_LONG_UK.sub(
            lambda m: f", що {m.group(1).strip()}", text, count=1
        )
    elif base == "ru":
        result = _RELATIVE_LONG_RU.sub(
            lambda m: f", который {m.group(1).strip()}", text, count=1
        )
    else:
        return None
    if result != text and len(result) < len(text):
        return " ".join(result.split())
    return None


# 5. Remove hedging/intensifier adverbs: "справді", "дійсно", "насправді", "буквально"
_HEDGE_ADVERBS_UK = re.compile(
    r"\b(справді|дійсно|насправді|фактично|буквально|взагалі\s+кажучи"
    r"|по\s+суті|в\s+принципі)\s+",
    re.IGNORECASE | re.UNICODE,
)
_HEDGE_ADVERBS_RU = re.compile(
    r"\b(действительно|буквально|фактически|на\s+самом\s+деле"
    r"|в\s+принципе|вообще\s+говоря|по\s+сути)\s+",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_remove_hedges(text: str, lang: str) -> str | None:
    """Remove redundant hedging adverbs that don't add meaning."""
    base = (lang or "uk").split("-")[0].lower()
    pat = (
        _HEDGE_ADVERBS_UK
        if base == "uk"
        else _HEDGE_ADVERBS_RU if base == "ru" else None
    )
    if pat is None:
        return None
    result = pat.sub("", text).strip()
    result = " ".join(result.split())
    if result and result != text.strip() and len(result.split()) >= 2:
        # Capitalize first word if needed
        if result[0].islower():
            result = result[0].upper() + result[1:]
        return result
    return None


# 6. Compress purpose clause: "щоб спробувати отримати" → "щоб отримати"
_PURPOSE_DOUBLE_UK = re.compile(
    r"\bщоб\s+(?:спробувати|намагатися|постаратися)\s+(\w+ти)\b",
    re.IGNORECASE | re.UNICODE,
)
_PURPOSE_DOUBLE_RU = re.compile(
    r"\bчтобы\s+(?:попробовать|пытаться|стараться|попытаться)\s+(\w+ть)\b",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_compress_purpose(text: str, lang: str) -> str | None:
    """'щоб спробувати потрапити' → 'щоб потрапити'."""
    base = (lang or "uk").split("-")[0].lower()
    pat = (
        _PURPOSE_DOUBLE_UK
        if base == "uk"
        else (_PURPOSE_DOUBLE_RU if base == "ru" else None)
    )
    if pat is None:
        return None
    result = pat.sub(r"щоб \1" if base == "uk" else r"чтобы \1", text, count=1)
    if result != text:
        return " ".join(result.split())
    return None


# 7. Remove redundant possessive: "своє рідне" → "рідне", "свій власний" → "власний"
_REDUNDANT_POSS_UK = re.compile(
    r"\b(?:своє?\s+)?(рідне?|особисте?|власне?)\b",
    re.IGNORECASE | re.UNICODE,
)
_REDUNDANT_POSS_RU = re.compile(
    r"\b(?:своё?\s+|свой?\s+|своя\s+)?(родной?|личный?|собственный?)\b",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_remove_redundant_possessive(text: str, lang: str) -> str | None:
    """'через своє рідне місто' → 'через рідне місто'."""
    base = (lang or "uk").split("-")[0].lower()
    pat = (
        _REDUNDANT_POSS_UK
        if base == "uk"
        else (_REDUNDANT_POSS_RU if base == "ru" else None)
    )
    if pat is None:
        return None
    result = pat.sub(r"\1", text, count=2)
    result = " ".join(result.split())
    if result and result != text.strip() and len(result) < len(text) - 2:
        return result
    return None


# 8. Temporal context removal: "в цей момент", "на даний час" → remove
_TEMPORAL_FILLER_UK = re.compile(
    r"\b(?:в\s+цей\s+момент|на\s+даний\s+час|на\s+той\s+момент"
    r"|в\s+той\s+час|в\s+ту\s+мить|тоді|тепер\s+же)\s*[,]?\s*",
    re.IGNORECASE | re.UNICODE,
)
_TEMPORAL_FILLER_RU = re.compile(
    r"\b(?:в\s+этот\s+момент|в\s+то\s+время|на\s+тот\s+момент"
    r"|в\s+тот\s+момент|в\s+данный\s+момент|тогда\s+же)\s*[,]?\s*",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_remove_temporal_filler(text: str, lang: str) -> str | None:
    """'Джордж, який в цей момент одужував' → 'Джордж, який одужував'."""
    base = (lang or "uk").split("-")[0].lower()
    pat = (
        _TEMPORAL_FILLER_UK
        if base == "uk"
        else (_TEMPORAL_FILLER_RU if base == "ru" else None)
    )
    if pat is None:
        return None
    result = pat.sub(" ", text).strip()
    result = " ".join(result.split())
    if result and result != text.strip() and len(result) < len(text) - 3:
        return result
    return None


# 9. Active-voice compression for "міг/не міг зрозуміти" → "не розумів"
_MODAL_COMPRESS_UK = re.compile(
    r"\b(не\s+)?міг\s+(зрозуміти|усвідомити|второпати)\b",
    re.IGNORECASE | re.UNICODE,
)
_MODAL_COMPRESS_RU = re.compile(
    r"\b(не\s+)?мог\s+(понять|осознать|уразуметь)\b",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_compress_modal(text: str, lang: str) -> str | None:
    """'не міг зрозуміти' → 'не розумів'."""
    base = (lang or "uk").split("-")[0].lower()
    if base == "uk":
        result = _MODAL_COMPRESS_UK.sub(
            lambda m: ("не розумів" if m.group(1) else "зрозумів"), text, count=1
        )
    elif base == "ru":
        result = _MODAL_COMPRESS_RU.sub(
            lambda m: ("не понимал" if m.group(1) else "понял"), text, count=1
        )
    else:
        return None
    if result != text:
        return " ".join(result.split())
    return None


# 10. Take only main clause from compound sentence with gerund
def _rephrase_main_clause_only(text: str, lang: str) -> str | None:
    """
    'A, B-герундій C.' → 'A.'  (take main clause only)
    Works when there's a comma before a gerund phrase in the middle/end.
    """
    base = (lang or "uk").split("-")[0].lower()
    if base == "uk":
        pat = _TRAILING_GERUND_UK
    elif base == "ru":
        pat = _TRAILING_GERUND_RU
    else:
        return None
    m = pat.search(text)
    if m and m.start() > 10:
        main = text[: m.start()].strip().rstrip(",")
        if main and len(main.split()) >= 3:
            if main[-1] not in ".!?":
                main += "."
            return main
    return None


# 11. EN: passive → active simplification
_PASSIVE_EN = re.compile(
    r"\b(was|were|is|are|has\s+been|have\s+been)\s+(\w+ed)\s+by\s+(\w+)",
    re.IGNORECASE,
)


def _rephrase_active_en(text: str, lang: str) -> str | None:
    """'X was done by Y' → 'Y did X'."""
    if (lang or "en").split("-")[0].lower() != "en":
        return None
    m = _PASSIVE_EN.search(text)
    if m:
        agent = m.group(3)
        verb_base = m.group(2)[:-2] if m.group(2).endswith("ed") else m.group(2)
        result = _PASSIVE_EN.sub(f"{agent} {verb_base}", text, count=1)
        if result != text:
            return " ".join(result.split())
    return None


# 12. Compress "вирішив, що він більше не хоче" → "вирішив більше не"
_DECIDED_UK = re.compile(
    r"\b(вирішив|вирішила|вирішили|зрозумів|зрозуміла)\s*,\s*що\s+він\s+"
    r"((?:більше\s+)?не\s+хоче\s+\w+)",
    re.IGNORECASE | re.UNICODE,
)
_DECIDED_RU = re.compile(
    r"\b(решил|решила|решили|понял|поняла)\s*,\s*что\s+он\s+"
    r"((?:больше\s+)?не\s+хочет\s+\w+)",
    re.IGNORECASE | re.UNICODE,
)


def _rephrase_compress_decided(text: str, lang: str) -> str | None:
    """'вирішив, що він більше не хоче брати участь' → 'вирішив більше не брати участь'."""
    base = (lang or "uk").split("-")[0].lower()
    if base == "uk":
        result = _DECIDED_UK.sub(r"\1 \2", text, count=1)
    elif base == "ru":
        result = _DECIDED_RU.sub(r"\1 \2", text, count=1)
    else:
        return None
    if result != text:
        return " ".join(result.split())
    return None


# 13. Combine: fronted reframe (original _step_reframe variants)
def _rephrase_fronted_clause(text: str, lang: str) -> str | None:
    """Legacy fronted-clause move (included in multi-variant pool)."""
    return _step_reframe(text, lang)


# ─── All rule-based variants ───────────────────────────────────────────────────

_RULE_REPHRASE_FUNCTIONS = [
    (_rephrase_remove_trailing_gerund, "remove_gerund"),
    (_rephrase_main_clause_only, "main_clause_only"),
    (_rephrase_compress_apposition, "compress_apposition"),
    (_rephrase_compress_location, "compress_location"),
    (_rephrase_compress_relative, "compress_relative"),
    (_rephrase_remove_hedges, "remove_hedges"),
    (_rephrase_compress_purpose, "compress_purpose"),
    (_rephrase_remove_redundant_possessive, "remove_poss"),
    (_rephrase_remove_temporal_filler, "remove_temporal"),
    (_rephrase_compress_modal, "compress_modal"),
    (_rephrase_compress_decided, "compress_decided"),
    (_rephrase_fronted_clause, "fronted_clause"),
    (_rephrase_active_en, "active_en"),
]


def _generate_rephrase_variants(
    text: str,
    lang: str,
    source_hint: str = "",
) -> list[tuple[str, str]]:
    """
    Generate all rule-based rephrase variants, including 2-rule combinations.

    Returns [(candidate_text, strategy_name), ...] with no duplicates.
    Single-rule variants come first; combined (2-rule) variants come after.
    """
    seen: set[str] = {text.strip()}
    singles: list[tuple[str, str]] = []

    for fn, name in _RULE_REPHRASE_FUNCTIONS:
        try:
            result = fn(text, lang)
        except Exception:
            result = None
        if result:
            result = " ".join(result.split())
            if result not in seen and len(result) > 4:
                seen.add(result)
                singles.append((result, name))

    # 2-rule combinations: apply a second rule on top of each single result
    combined: list[tuple[str, str]] = []
    for r1_text, r1_name in singles:
        for fn2, n2 in _RULE_REPHRASE_FUNCTIONS:
            if n2 == r1_name:
                continue  # don't apply same rule twice
            try:
                r2 = fn2(r1_text, lang)
            except Exception:
                r2 = None
            if r2:
                r2 = " ".join(r2.split())
                if r2 not in seen and len(r2) > 4:
                    seen.add(r2)
                    combined.append((r2, f"{r1_name}+{n2}"))

    return singles + combined


# ─── LLM multi-variant rephrase ───────────────────────────────────────────────


def _llm_rephrase_variants(
    text: str,
    lang: str,
    slot_ms: int,
    source_hint: str,
    n: int = 7,
) -> list[tuple[str, str]]:
    """
    Generate n structurally different rephrase variants via LLM.
    Returns [(candidate_text, 'llm_A'), (text, 'llm_B'), ...].

    Each variant must:
    - preserve full meaning
    - use different sentence structure
    - be shorter than the original
    """
    if not text.strip():
        return []
    from engines.ai_core import llm_gateway

    if not llm_gateway.is_available():
        return []

    try:
        _LANG_NAMES = {
            "ru": "Russian",
            "uk": "Ukrainian",
            "en": "English",
            "de": "German",
            "fr": "French",
            "es": "Spanish",
            "pl": "Polish",
        }
        base = (lang or "en").split("-")[0].lower()
        lang_label = _LANG_NAMES.get(base, base)

        system = (
            f"You are a professional dubbing editor for {lang_label} voice-over.\n"
            f"Generate EXACTLY {n} different rephrasings of the given sentence.\n"
            "REQUIREMENTS for each rephrasing:\n"
            "  1. Preserve ALL key facts and meaning (names, numbers, actions, locations, times).\n"
            f"  2. Sound natural in spoken {lang_label} — as a native speaker would say it.\n"
            "  3. Use a DIFFERENT sentence structure from the others (don't just swap words).\n"
            "  4. Be measurably shorter than the original — shorter sentence structure, not just synonyms.\n"
            "     Methods: remove participial/gerund phrases, compress relative clauses,\n"
            "     use active voice, remove redundant qualifiers, restructure compound sentences.\n"
            "  5. Do NOT add anything new. Do NOT remove names/actions/key nouns/negations/numbers.\n"
            f"OUTPUT FORMAT (strictly, no other text):\n"
            "A: <rephrasing>\n"
            "B: <rephrasing>\n"
            f"... up to {chr(ord('A') + n - 1)}: <rephrasing>"
        )

        parts: list[str] = []
        if source_hint.strip():
            parts.append(f"Original speech: {source_hint.strip()}")
        parts.append(f"Current {lang_label} text:\n{text.strip()}")

        # Single gateway (AI Core): cached, budgeted, logged, anti-truncation.
        content = llm_gateway.chat(
            "\n\n".join(parts),
            system=system,
            temperature=0.7,
            max_tokens=700,
            timeout=25,
        )
        if not content:
            # Empty reply is already recorded as a hard failure by the transport
            # (_llm_chat ok=bool(out)) — it feeds the global circuit breaker.
            return []
        content = content.strip()
        variants: list[tuple[str, str]] = []
        from engines.sentence_integrity import validate_tts_text

        for line in content.split("\n"):
            line = line.strip()
            parsed = re.match(r"^([A-J]):\s+(.+)$", line)
            if parsed:
                label = parsed.group(1)
                variant_text = parsed.group(2).strip().strip("\"'«»")
                if not variant_text or len(variant_text) <= 5:
                    continue
                # Never let a clipped / unfinished variant enter selection.
                ok, _iss = validate_tts_text(variant_text)
                if not ok:
                    continue
                variants.append((variant_text, f"llm_{label}"))

        # Salvage: many small local models ignore the "A:/B:" format and return
        # a single unlabeled rewrite. Rather than discarding a usable response
        # (which caused the "0 candidates" symptom), accept plausible unlabeled
        # lines as candidates too. The same validation gate applies.
        if not variants:
            for line in content.split("\n"):
                cand = line.strip().strip("\"'«»").lstrip("-•*0123456789. ").strip()
                if len(cand) <= 5 or cand == text.strip():
                    continue
                ok, _iss = validate_tts_text(cand)
                if ok:
                    variants.append((cand, f"llm_{chr(ord('A') + len(variants))}"))
                if len(variants) >= n:
                    break

        if not variants:
            # The LLM responded but produced nothing usable. Report it so
            # repeated useless output trips the global circuit breaker instead of
            # every segment paying the full slow-LLM cost (P0 no-hang / no-stall).
            try:
                from engines.translation_adapt import record_llm_unusable

                record_llm_unusable("ada_no_usable_variants")
            except Exception:
                pass
        return variants[:n]

    except Exception as exc:
        logger.debug("[ADA] LLM variants skipped: %s", exc)
        return []


# ─── Best-variant selector ─────────────────────────────────────────────────────

_MIN_REDUCTION_PCT: float = 5.0  # variant must reduce duration by at least 5%


def _select_best_rephrase(
    candidates: list[tuple[str, str]],
    original: str,
    lang: str,
    slot_ms: int,
    segment_index: int = -1,
) -> tuple[str | None, str, list[dict]]:
    """
    Select the best rephrase variant using a weighted scoring system.
    Returns (best_text | None, strategy_name, variant_log)
    """
    orig_ms = predict_duration_ms(original, lang)
    variant_log: list[dict] = []

    orig_set = _semantic_word_set(original)
    orig_proper = {w for w in re.findall(r"[А-ЯІЇЄA-Z][а-яіїєa-z\w]{3,}", original)}
    orig_nums = set(re.findall(r"\b\d+\b", original))

    scored_candidates = []

    for cand_text, strategy in candidates:
        if not (cand_text or "").strip() or cand_text.strip() == original.strip():
            variant_log.append(
                {
                    "strategy": strategy,
                    "text": cand_text,
                    "predicted_ms": orig_ms,
                    "reduction_pct": 0.0,
                    "Meaning Score": 0,
                    "Timing Score": 0,
                    "Grammar Score": 0,
                    "Naturalness Score": 0,
                    "Entity Score": 0,
                    "Slot Fit Score": 0,
                    "rejected": True,
                    "reject_reason": "identical_or_empty",
                }
            )
            continue

        cand_set = _semantic_word_set(cand_text)
        cand_proper = {
            w for w in re.findall(r"[А-ЯІЇЄA-Z][а-яіїєa-z\w]{3,}", cand_text)
        }
        cand_nums = set(re.findall(r"\b\d+\b", cand_text))

        # Calculate Meaning Score (0-100)
        overlap = len(orig_set & cand_set)
        meaning_score = int(100 * overlap / len(orig_set)) if orig_set else 100

        # Calculate Entity Score (0-100)
        total_entities = len(orig_proper) + len(orig_nums)
        if total_entities == 0:
            entity_score = 100
        else:
            entity_overlap = len(orig_proper & cand_proper) + len(orig_nums & cand_nums)
            entity_score = int(100 * entity_overlap / total_entities)

        pred_ms = predict_duration_ms(cand_text, lang)
        reduction_pct = 100.0 * (orig_ms - pred_ms) / max(orig_ms, 1)

        # Slot Fit Score (0-100)
        if slot_ms > 0:
            if pred_ms <= slot_ms:
                slot_fit_score = 100
            else:
                overflow_pct = 100.0 * (pred_ms - slot_ms) / slot_ms
                slot_fit_score = max(0, int(100 - overflow_pct * 2))
        else:
            slot_fit_score = 100

        # Timing Score (0-100) - based on reduction
        timing_score = min(100, int(max(0, reduction_pct) * 2))

        # Grammar and Naturalness scores are heuristic (LLM gets slight bump, rules get fixed)
        grammar_score = 95 if strategy.startswith("llm") else 90
        naturalness_score = 95 if strategy.startswith("llm") else 85

        if meaning_score < 75 or entity_score < 100:
            variant_log.append(
                {
                    "strategy": strategy,
                    "text": cand_text,
                    "predicted_ms": pred_ms,
                    "reduction_pct": round(reduction_pct, 1),
                    "Meaning Score": meaning_score,
                    "Timing Score": timing_score,
                    "Grammar Score": grammar_score,
                    "Naturalness Score": naturalness_score,
                    "Entity Score": entity_score,
                    "Slot Fit Score": slot_fit_score,
                    "rejected": True,
                    "reject_reason": "meaning_or_entity_loss",
                }
            )
            continue

        if reduction_pct < _MIN_REDUCTION_PCT and slot_fit_score < 100:
            variant_log.append(
                {
                    "strategy": strategy,
                    "text": cand_text,
                    "predicted_ms": pred_ms,
                    "reduction_pct": round(reduction_pct, 1),
                    "Meaning Score": meaning_score,
                    "Timing Score": timing_score,
                    "Grammar Score": grammar_score,
                    "Naturalness Score": naturalness_score,
                    "Entity Score": entity_score,
                    "Slot Fit Score": slot_fit_score,
                    "rejected": True,
                    "reject_reason": f"reduction_only_{reduction_pct:.1f}pct_<_{_MIN_REDUCTION_PCT}pct",
                }
            )
            continue

        # Total score for ranking
        total_score = (
            meaning_score * 0.2
            + entity_score * 0.2
            + slot_fit_score * 0.4
            + naturalness_score * 0.2
        )

        entry = {
            "strategy": strategy,
            "text": cand_text,
            "predicted_ms": pred_ms,
            "reduction_pct": round(reduction_pct, 1),
            "Meaning Score": meaning_score,
            "Timing Score": timing_score,
            "Grammar Score": grammar_score,
            "Naturalness Score": naturalness_score,
            "Entity Score": entity_score,
            "Slot Fit Score": slot_fit_score,
            "total_score": total_score,
            "rejected": False,
            "reject_reason": None,
            "fits": pred_ms <= slot_ms * 1.15,
        }
        variant_log.append(entry)
        scored_candidates.append(entry)

    if not scored_candidates:
        logger.info(
            "[ADA] Seg %d: no valid rephrase from %d candidates",
            segment_index,
            len(candidates),
        )
        return None, "none", variant_log

    # Select best option based on total_score
    best_entry = max(scored_candidates, key=lambda x: x["total_score"])
    best_text = best_entry["text"]
    best_strategy = best_entry["strategy"]

    for entry in variant_log:
        if entry.get("text") == best_text and entry.get("strategy") == best_strategy:
            entry["chosen"] = True

    logger.info(
        "[ADA] Seg %d: CHOSEN rephrase %s | %dms→%dms | saved=%.1f%% | TotalScore=%.1f",
        segment_index,
        best_strategy,
        orig_ms,
        best_entry["predicted_ms"],
        best_entry["reduction_pct"],
        best_entry["total_score"],
    )
    return best_text, best_strategy, variant_log


def _log_rephrase_decision(
    variant_log: list[dict],
    original: str,
    segment_index: int,
    slot_ms: int,
    lang: str,
) -> None:
    """Write a human-readable rephrase decision log."""
    orig_ms = predict_duration_ms(original, lang)
    lines = [
        f"",
        f"[ADA] Rephrase decision — Seg {segment_index} | "
        f"Original: {orig_ms}ms | Slot: {slot_ms}ms",
        f"  Original text: {original!r}",
    ]
    for entry in variant_log:
        status = (
            "CHOSEN"
            if entry.get("chosen")
            else ("REJECTED" if entry.get("rejected") else "valid")
        )
        reason = (
            entry.get("reject_reason") or f"reduction={entry.get('reduction_pct', 0)}%"
        )
        lines.append(
            f"  [{status:8s}] {entry['strategy']:25s} | "
            f"{entry['predicted_ms']}ms | {reason} | {str(entry.get('text', ''))[:60]!r}"
        )
    for line in lines:
        logger.info("%s", line)


# ─── Step 3: Synonyms ─────────────────────────────────────────────────────────


def _step_synonyms(text: str, lang: str) -> str | None:
    """Replace long words/phrases with shorter natural synonyms."""
    base = (lang or "en").split("-")[0].lower()
    table = {**_SYNONYMS.get("*", {}), **_SYNONYMS.get(base, {})}
    if not table:
        return None
    out = text
    changed = False
    # Multi-word phrases first (longest first to avoid partial matches)
    for long_form, short_form in sorted(table.items(), key=lambda x: -len(x[0])):
        if not long_form:
            continue
        new = re.sub(
            r"(?<![а-яёА-ЯЁіІїЇєЄa-zA-Z0-9])"
            + re.escape(long_form)
            + r"(?![а-яёА-ЯЁіІїЇєЄa-zA-Z0-9])",
            short_form,
            out,
            flags=re.IGNORECASE,
        )
        if new != out:
            out = new
            changed = True
    return " ".join(out.split()) if changed else None


# ─── Step 4: Word order ───────────────────────────────────────────────────────

# Temporal / locative adverbials that can safely move to sentence end
_MOVEABLE_ADVERBS_RU = re.compile(
    r"^(Сегодня|Вчера|Завтра|Здесь|Там|Тогда|Потом|Сначала|Сейчас|"
    r"Уже|Ещё|Снова|Опять|Давно|Скоро)\s+(.{8,})$",
    re.IGNORECASE,
)
_MOVEABLE_ADVERBS_UK = re.compile(
    r"^(Сьогодні|Вчора|Завтра|Тут|Там|Тоді|Потім|Спочатку|Зараз|"
    r"Вже|Ще|Знову|Знов|Давно|Скоро)\s+(.{8,})$",
    re.IGNORECASE,
)


def _step_word_order(text: str, lang: str) -> str | None:
    """
    Move leading time/location adverbials to the end.
    'Сегодня он пришёл домой' → 'Он пришёл домой сегодня'
    """
    base = (lang or "en").split("-")[0].lower()
    if base not in ("ru", "uk"):
        return None  # English word order is rigid
    pattern = _MOVEABLE_ADVERBS_RU if base == "ru" else _MOVEABLE_ADVERBS_UK
    m = pattern.match(text.strip())
    if m:
        adverb = m.group(1)
        rest = m.group(2).strip()
        # Move adverb to end, removing period first if present
        rest_stripped = rest.rstrip(".!?…")
        end_punct = text.strip()[len(m.group(1)) + 1 + len(rest_stripped) :] or ""
        candidate = f"{rest_stripped} {adverb.lower()}{end_punct}".strip()
        candidate = " ".join(candidate.split())
        if candidate and candidate != text:
            return candidate
    return None


# ─── Step 5: Remove secondary words ──────────────────────────────────────────


def _step_remove_secondary(text: str, lang: str, source_hint: str) -> str | None:
    """
    Remove ONLY non-semantic words:
      - introductory filler phrases at sentence start
      - trailing filler expressions
      - redundant intensifiers before adjectives

    NEVER removes: names, actions, key nouns, negations, numbers, dates.
    """
    base = (lang or "en").split("-")[0].lower()
    out = text

    # Sentence-initial fillers
    filler_re = (
        _SENTENCE_FILLERS_RU
        if base == "ru"
        else _SENTENCE_FILLERS_UK if base == "uk" else _SENTENCE_FILLERS_EN
    )
    after_intro = filler_re.sub("", out).strip()
    if after_intro and after_intro != out:
        # Capitalise first word if needed
        if after_intro[0].islower():
            after_intro = after_intro[0].upper() + after_intro[1:]
        # Make sure we didn't accidentally remove a protected word
        removed_part = out[: len(out) - len(after_intro)]
        has_protected = any(
            _contains_protected_word(w, source_hint) for w in removed_part.split()
        )
        if not has_protected:
            out = after_intro

    # Trailing fillers
    trail_re = (
        _TRAILING_FILLER_RU
        if base == "ru"
        else _TRAILING_FILLER_UK if base == "uk" else _TRAILING_FILLER_EN
    )
    after_trail = trail_re.sub("", out).strip()
    if after_trail and after_trail != out and len(after_trail.split()) >= 2:
        out = after_trail

    # Redundant intensifiers before adjectives (do NOT remove before verbs)
    _INTENSIFIER = re.compile(
        r"\b(очень|слишком|весьма|крайне|чрезвычайно|"
        r"дуже|надто|вкрай|надзвичайно|"
        r"very|extremely|incredibly|terribly)\s+(?=[А-ЯA-Zа-яa-z])",
        re.IGNORECASE,
    )
    # Only remove if it precedes an adjective, not before a verb like 'нравится'
    _ADJ_AFTER = re.compile(
        r"\b(очень|слишком|весьма|крайне|чрезвычайно|"
        r"дуже|надто|вкрай|надзвичайно|"
        r"very|extremely|incredibly|terribly)\s+"
        r"([а-яёА-ЯЁіІїЇєЄa-zA-Z]{3,}(?:ный|ной|ний|ній|ый|ий|ой|ій|'?s)?)\b",
        re.IGNORECASE,
    )
    after_intens = _ADJ_AFTER.sub(r"\2", out)
    if after_intens and after_intens != out and len(after_intens.split()) >= 2:
        out = " ".join(after_intens.split())

    # Remove duplicate consecutive words (e.g., "the the", "и и")
    _DUP = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)
    out = _DUP.sub(r"\1", out)

    out = " ".join(out.split())
    return out if out != text else None


# ─── LLM reframe (optional, only if API key available) ────────────────────────


def _llm_reframe(
    text: str,
    lang: str,
    slot_ms: int,
    source_hint: str,
) -> str | None:
    """
    Backward-compatible single-variant LLM rephrase.
    Delegates to _llm_rephrase_variants and returns the first valid result.
    """
    variants = _llm_rephrase_variants(text, lang, slot_ms, source_hint, n=1)
    return variants[0][0] if variants else None


# ─── Pre-TTS validation ───────────────────────────────────────────────────────

_TECH_TOKEN_RE = re.compile(
    r"\b(?:xmlns|http[s]?://|www\.|<\w+|&\w+;|" r"[A-Z0-9]{6,})\b"
)
_BROKEN_WORD_RE = re.compile(r"[^\w\s\.,!?;:—–\-\'\"«»()\d]")

_DUP_WORD_RE = re.compile(
    r"(?<!\w)([\wа-яА-ЯіІїЇєЄёЁ'-]{3,})\s+\1(?!\w)",
    re.IGNORECASE,
)


def validate_pre_tts(
    text: str,
    original: str,
    source_hint: str,
    slot_ms: int,
    lang: str,
) -> tuple[bool, list[str]]:
    """
    Final pre-TTS gate. Returns (ok, [list_of_issues]).

    Checks (per TZ):
    • text is not empty
    • no technical tokens / broken markup
    • meaning preserved (word retention ≥ threshold)
    • predicted duration fits within slot (hard warn, not block)
    • no broken / corrupted words
    • no repetitions
    """
    notes: list[str] = []
    ok = True

    if not (text or "").strip():
        return False, ["empty_text"]

    # Technical tokens
    if _TECH_TOKEN_RE.search(text):
        notes.append("tech_tokens_detected")
        ok = False

    # Word retention
    if original.strip() and not _word_retention_ok(original, text):
        notes.append("word_retention_below_threshold")
        ok = False

    # Duration fit (warning only — don't block TTS)
    if slot_ms > 0:
        predicted = predict_duration_ms(text, lang)
        overflow_pct = 100.0 * max(0, predicted - slot_ms) / max(slot_ms, 1)
        if overflow_pct > _HARD_OVERFLOW_WARN_PCT:
            notes.append(f"duration_overflow_{int(overflow_pct)}pct")
            # Do NOT set ok=False — we warn but proceed

    # Duplicate words
    if _DUP_WORD_RE.search(text):
        notes.append("duplicate_words_detected")
        # Auto-fix duplicates
        text = _DUP_WORD_RE.sub(r"\1", text)

    return ok, notes


# ─── Semantic word retention check ───────────────────────────────────────────


def _semantic_word_set(text: str) -> frozenset[str]:
    """
    Return the set of "semantic" words: 4+ char words, excluding common
    short function words (prepositions, conjunctions, particles).
    Used for 85% meaning retention check.
    """
    SKIP = frozenset(
        {
            # Ukrainian
            "але",
            "або",
            "щоб",
            "якщо",
            "коли",
            "хоча",
            "через",
            "поки",
            "між",
            "при",
            "над",
            "під",
            "про",
            "для",
            "від",
            "без",
            "після",
            "перед",
            # Russian
            "что",
            "это",
            "как",
            "так",
            "уже",
            "ещё",
            "даже",
            "тоже",
            "если",
            "чтобы",
            "когда",
            "хотя",
            "после",
            "перед",
            "между",
            "через",
            "около",
            # English
            "that",
            "this",
            "with",
            "from",
            "have",
            "been",
            "were",
            "they",
            "their",
            "there",
            "which",
            "when",
            "what",
            "where",
            "then",
            "than",
            # German
            "dass",
            "wenn",
            "aber",
            "oder",
            "durch",
            "nach",
            "über",
            "unter",
        }
    )
    words = re.findall(r"[\w']{4,}", text or "", flags=re.UNICODE)
    return frozenset(w.lower() for w in words if w.lower() not in SKIP)


def _meaning_retained(original: str, adapted: str) -> bool:
    """
    Return True if adapted text retains ≥ 85% of semantic information from original.

    For structural rewrites (apposition compression, clause merging), the threshold
    is relaxed to 75% IF all proper names and numbers from the original are present
    in the adapted version — because structural rewrites replace noun+name with just
    name, which preserves 100% of the actual semantic content.
    """
    orig_set = _semantic_word_set(original)
    if not orig_set:
        return True
    adpt_set = _semantic_word_set(adapted)
    overlap = len(orig_set & adpt_set)
    retention = overlap / len(orig_set)

    if retention >= _MIN_WORD_RETENTION:
        return True

    # Structural-rewrite bonus: if all proper names and numbers are preserved,
    # allow 75% threshold (structural rewrite legitimately replaces generic
    # nouns with specific names, e.g., "хлопець на ім'я X" → "X")
    if retention >= 0.72:
        # Check all capitalized words (4+ chars) from original are present
        orig_proper = {w for w in re.findall(r"[А-ЯІЇЄA-Z][а-яіїєa-z\w]{3,}", original)}
        adpt_proper = {w for w in re.findall(r"[А-ЯІЇЄA-Z][а-яіїєa-z\w]{3,}", adapted)}
        # Check all numbers preserved
        orig_nums = set(re.findall(r"\b\d+\b", original))
        adpt_nums = set(re.findall(r"\b\d+\b", adapted))
        if (orig_proper <= adpt_proper or not orig_proper) and orig_nums <= adpt_nums:
            return True  # Structural rewrite preserves proper names + numbers

    return False


def _word_retention_ok(original: str, adapted: str) -> bool:
    """Delegate to meaning_retained (backward-compatible name)."""
    return _meaning_retained(original, adapted)


# ─── Main adapter function ────────────────────────────────────────────────────


def adapt_segment(
    text: str,
    *,
    slot_ms: int,
    lang: str = "ru",
    source_hint: str = "",
    segment_index: int = -1,
) -> "AdaptResult":
    """
    Adapt ONE segment for TTS.

    Decision tree (per ТЗ — strictly in this order):
      0. Predict → if ratio < 1.15:  PASS, zero changes allowed.
      1. Predict → if ratio < 1.25:  only Reframe / Synonyms / Word Order.
      2. Predict → if ratio ≥ 1.25:  Secondary reduction allowed.
      3. After ALL steps failed:      LLM fallback (optional).

    Rules enforced:
      • 85% semantic word retention at every step.
      • Never remove: actions, locations, times, names, negations, numbers.
      • Log every prediction + decision for every step.
      • No character counting — syllable predictor only.
    """
    t0 = time.perf_counter()
    original = " ".join(str(text or "").split())
    if not original:
        return AdaptResult(
            original=original,
            text=original,
            fits=True,
            predicted_ms=0,
            slot_ms=slot_ms,
            natural_pause_ms=_DEFAULT_PAUSE_MS,
            lang=lang,
        )

    current = original
    steps: list[AdaptStep] = []
    iteration = [0]  # mutable counter for logging

    def _predict_current() -> tuple[int, float]:
        """Return (predicted_ms, overflow_pct) for current text."""
        ms = predict_duration_ms(current, lang)
        opct = _overflow_pct(current, lang, slot_ms)
        return ms, opct

    def _log_predict(step_name: str, reason: str = "") -> tuple[int, float]:
        """Record a prediction step and return (ms, overflow_pct)."""
        ms, opct = _predict_current()
        iteration[0] += 1
        decision = (
            "PASS"
            if opct < _PASS_THRESHOLD_PCT
            else ("REPHRASE" if opct < _REDUCE_THRESHOLD_PCT else "REDUCE")
        )
        log_line = (
            f"[ADA] Seg {segment_index} Iter {iteration[0]} | "
            f"Step: {step_name} | "
            f"Target: {slot_ms}ms | "
            f"Prediction: {ms}ms | "
            f"Diff: +{max(0, ms - slot_ms)}ms | "
            f"Overflow: {opct:.1f}% | "
            f"Decision: {decision}"
        )
        if reason:
            log_line += f" | Reason: {reason}"
        logger.info(log_line)
        steps.append(
            AdaptStep(
                name=step_name,
                applied=False,
                text_before=current,
                text_after=current,
                predicted_ms_before=ms,
                predicted_ms_after=ms,
                reason=reason or decision,
            )
        )
        return ms, opct

    def _try_apply(step_name: str, candidate: str | None, reason: str = "") -> bool:
        """
        Validate and apply candidate text.
        Returns True if applied, False if rejected.
        Rejection reasons: no change, meaning lost (< 85% retention).
        """
        nonlocal current
        if not candidate or candidate == current:
            ms_now = predict_duration_ms(current, lang)
            steps.append(
                AdaptStep(
                    name=step_name,
                    applied=False,
                    text_before=current,
                    text_after=current,
                    predicted_ms_before=ms_now,
                    predicted_ms_after=ms_now,
                    reason="no_change",
                )
            )
            return False

        ms_before = predict_duration_ms(current, lang)
        ms_after = predict_duration_ms(candidate, lang)
        saved_ms = ms_before - ms_after

        # ── 85% meaning retention gate ─────────────────────────────────────
        if not _meaning_retained(original, candidate):
            logger.warning(
                "[ADA] Seg %d: REJECTED %s — meaning loss (retention < 85%%): %r → %r",
                segment_index,
                step_name,
                current[:60],
                candidate[:60],
            )
            steps.append(
                AdaptStep(
                    name=step_name,
                    applied=False,
                    text_before=current,
                    text_after=current,
                    predicted_ms_before=ms_before,
                    predicted_ms_after=ms_before,
                    reason="rejected_meaning_loss",
                )
            )
            return False

        steps.append(
            AdaptStep(
                name=step_name,
                applied=True,
                text_before=current,
                text_after=candidate,
                predicted_ms_before=ms_before,
                predicted_ms_after=ms_after,
                reason=(
                    f"{reason} | saved={saved_ms}ms"
                    if reason
                    else f"applied | saved={saved_ms}ms"
                ),
            )
        )
        logger.info(
            "[ADA] Seg %d: APPLIED %s | %dms → %dms | saved=%dms | %r → %r",
            segment_index,
            step_name,
            ms_before,
            ms_after,
            saved_ms,
            current[:60],
            candidate[:60],
        )
        current = candidate
        return True

    # ── Step 0: Predict ───────────────────────────────────────────────────────
    ms_init, opct_init = _log_predict("predict_initial", "initial_check")

    # PASS: ratio < 1.15 → zero changes allowed
    if opct_init < _PASS_THRESHOLD_PCT:
        val_ok, val_notes = validate_pre_tts(
            current, original, source_hint, slot_ms, lang
        )
        return AdaptResult(
            original=original,
            text=current,
            fits=True,
            predicted_ms=ms_init,
            slot_ms=slot_ms,
            natural_pause_ms=natural_pause_ms(current),
            lang=lang,
            steps=steps,
            validation_ok=val_ok,
            validation_notes=val_notes,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    # ── Step 1: Multi-Variant Structural Rephrase (MAIN strategy) ───────────────
    # Generate all structural variants, evaluate each, pick the best.
    # Deletion FORBIDDEN until ALL rephrase variants are exhausted.
    rule_variants = _generate_rephrase_variants(current, lang, source_hint)
    llm_variants = _llm_rephrase_variants(current, lang, slot_ms, source_hint)
    all_rephrase = rule_variants + llm_variants

    best_rephrase, rephrase_strategy, rephrase_log = _select_best_rephrase(
        all_rephrase,
        original=original,
        lang=lang,
        slot_ms=slot_ms,
        segment_index=segment_index,
    )
    _log_rephrase_decision(rephrase_log, original, segment_index, slot_ms, lang)

    if best_rephrase:
        _try_apply(
            "rephrase", best_rephrase, f"structural_rephrase_{rephrase_strategy}"
        )
        _log_predict("predict_after_rephrase")

    # ── Step 2: Synonyms (supplementary) ─────────────────────────────────────
    if not fits_in_slot(current, lang, slot_ms):
        if _try_apply("synonyms", _step_synonyms(current, lang), "shorter_synonyms"):
            _log_predict("predict_after_synonyms")

    # ── Step 3: Word order ────────────────────────────────────────────────────
    if not fits_in_slot(current, lang, slot_ms):
        if _try_apply(
            "word_order", _step_word_order(current, lang), "fronted_adverb_moved"
        ):
            _log_predict("predict_after_word_order")

    # ── Check overflow BEFORE allowing secondary reduction ────────────────────
    # Secondary reduction allowed ONLY if overflow is still ≥ 25% after rephrase+synonyms.
    _, opct_now = _predict_current()
    if not fits_in_slot(current, lang, slot_ms) and opct_now >= _REDUCE_THRESHOLD_PCT:
        # ── Step 4: Remove secondary words (fillers only — LAST resort) ──────
        _try_apply(
            "remove_secondary",
            _step_remove_secondary(current, lang, source_hint),
            "fillers_removed",
        )
        _log_predict("predict_after_secondary")
    elif not fits_in_slot(current, lang, slot_ms) and opct_now < _REDUCE_THRESHOLD_PCT:
        # Overflow is 15-25% → secondary reduction FORBIDDEN
        logger.info(
            "[ADA] Seg %d: overflow=%.1f%% < %.0f%% — "
            "secondary reduction FORBIDDEN, passing as-is",
            segment_index,
            opct_now,
            _REDUCE_THRESHOLD_PCT,
        )
        _log_predict(
            "predict_no_secondary",
            f"overflow={opct_now:.1f}%_below_threshold",
        )

    predicted_final = predict_duration_ms(current, lang)
    fits_final = fits_in_slot(current, lang, slot_ms)

    if not fits_final:
        logger.warning(
            "[ADA] Seg %d: still overflows after all steps: predicted=%dms slot=%dms text=%r",
            segment_index,
            predicted_final,
            slot_ms,
            current[:80],
        )

    val_ok, val_notes = validate_pre_tts(current, original, source_hint, slot_ms, lang)

    return AdaptResult(
        original=original,
        text=current,
        fits=fits_final,
        predicted_ms=predicted_final,
        slot_ms=slot_ms,
        natural_pause_ms=natural_pause_ms(current),
        lang=lang,
        steps=steps,
        validation_ok=val_ok,
        validation_notes=val_notes,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
        variant_log=rephrase_log if "rephrase_log" in locals() else [],
    )


# ─── Batch adapter (main entry point) ─────────────────────────────────────────


def adapt_segments_for_tts(
    segments: list[str],
    *,
    timing_map: list | None = None,
    lang: str = "ru",
    source_hints: list[str] | None = None,
    app_dir: Path | None = None,
    task_id: str = "",
) -> tuple[list[str], dict[str, Any]]:
    """
    Batch-adapt all segments before TTS generation.

    Called from auto_dub_api._run_pipeline_inner between SSO and prepare_segments_for_tts.
    Does NOT modify: Whisper, Translation, Reader, Dub Studio, Export, Mixer, or UI.

    Returns (adapted_segments, meta_dict).
    """
    app_dir = app_dir or Path(__file__).resolve().parent.parent
    t0 = time.perf_counter()
    out: list[str] = []
    changed_count = 0
    overflows = 0
    validation_fails = 0
    step_counters: dict[str, int] = {}
    natural_pauses: list[int] = []
    # Full per-segment audit trail (original → adapted) for the pipeline trace log
    segment_audit: list[dict[str, Any]] = []

    n = len(segments)
    for i, seg_text in enumerate(segments):
        # Resolve slot_ms from timing_map (or use a generous default)
        slot_ms = 0
        if timing_map and i < len(timing_map):
            entry = timing_map[i]
            if isinstance(entry, dict):
                slot_ms = max(0, int(entry.get("end", 0)) - int(entry.get("start", 0)))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                slot_ms = max(0, int(entry[1]) - int(entry[0]))

        source_hint = source_hints[i] if source_hints and i < len(source_hints) else ""

        if slot_ms <= 0:
            # No timing information — pass through unchanged
            out.append(seg_text)
            natural_pauses.append(natural_pause_ms(seg_text))
            segment_audit.append(
                {
                    "index": i,
                    "original_text": source_hint,
                    "translated_text": seg_text,
                    "adapted_text": seg_text,
                    "text_sent_to_tts": seg_text,
                    "slot_ms": slot_ms,
                    "changed": False,
                    "fits": True,
                    "steps": [],
                }
            )
            continue

        # P0 no-hang: bound EACH segment's adaptation with a hard wall-clock
        # watchdog even on this legacy batch path (used when the DubbingEngine
        # is bypassed). adapt_segment issues an LLM call; the watchdog guarantees
        # the run advances even if that call is pathologically slow.
        def _run_adapt(_seg=seg_text, _slot=slot_ms, _hint=source_hint, _idx=i) -> "AdaptResult":
            return adapt_segment(
                _seg, slot_ms=_slot, lang=lang, source_hint=_hint, segment_index=_idx
            )

        def _fallback_adapt(_seg=seg_text, _slot=slot_ms, _idx=i) -> "AdaptResult":
            return AdaptResult(
                original=_seg,
                text=_seg,
                fits=fits_in_slot(_seg, lang, _slot),
                predicted_ms=predict_duration_ms(_seg, lang),
                slot_ms=_slot,
                natural_pause_ms=natural_pause_ms(_seg),
                lang=lang,
                validation_notes=["watchdog_timeout"],
            )

        try:
            from engines.pipeline_segment_watchdog import run_segment_bounded

            _watch = run_segment_bounded(
                task_id=task_id,
                phase="adaptive_dubbing_adapter",
                segment_index=i,
                fn=_run_adapt,
                fallback=_fallback_adapt,
            )
            result = _watch.value
        except Exception:
            result = _run_adapt()

        if result.changed:
            changed_count += 1
            ms_before = predict_duration_ms(seg_text, lang)
            saved = ms_before - result.predicted_ms
            applied_steps = [s.name for s in result.steps if s.applied]
            logger.info(
                "[ADA] seg#%d CHANGED | %dms → %dms | saved=%dms | steps=%s\n"
                "  Original:  %r\n"
                "  Adapted:   %r",
                i,
                ms_before,
                result.predicted_ms,
                saved,
                applied_steps,
                seg_text[:100],
                result.text[:100],
            )

        if not result.fits:
            overflows += 1
            logger.debug(
                "[ADA] seg#%d still overflows: predicted=%dms slot=%dms",
                i,
                result.predicted_ms,
                slot_ms,
            )

        if not result.validation_ok:
            validation_fails += 1
            logger.warning(
                "[ADA] seg#%d pre-TTS validation: %s",
                i,
                result.validation_notes,
            )

        for step in result.steps:
            if step.applied:
                step_counters[step.name] = step_counters.get(step.name, 0) + 1

        # ── Phonetic resolver: fix entity pronunciation before TTS ────────────
        adapted_text = result.text
        phonetic_changes: list[str] = []
        try:
            from engines.dubbing_engine.phonetics import resolve_phonetics

            adapted_text, phonetic_changes = resolve_phonetics(adapted_text, lang)
            if phonetic_changes:
                logger.info(
                    "[ADA] seg#%d phonetic fixes: %s",
                    i,
                    ", ".join(phonetic_changes),
                )
        except Exception as _e:
            logger.debug("[ADA] phonetics skipped: %s", _e)

        out.append(adapted_text)
        natural_pauses.append(result.natural_pause_ms)
        # ── Full 9-field audit row ─────────────────────────────────────────────
        ms_orig = predict_duration_ms(seg_text, lang)
        ms_final = predict_duration_ms(adapted_text, lang)
        applied_steps = [s.name for s in result.steps if s.applied]
        segment_audit.append(
            {
                "index": i,
                "original_text": source_hint,  # English whisper source
                "translated_text": seg_text,  # Input to ADA
                "adapted_text": result.text,  # ADA output (pre-phonetics)
                "text_sent_to_tts": adapted_text,  # Final text to TTS (post-phonetics)
                "slot_ms": slot_ms,
                "predicted_ms_before": ms_orig,
                "predicted_ms_after": ms_final,
                "saved_ms": max(0, ms_orig - ms_final),
                "changed": result.changed or bool(phonetic_changes),
                "fits": result.fits,
                "validation_ok": result.validation_ok,
                "validation_notes": result.validation_notes,
                "steps_applied": applied_steps,
                "phonetic_changes": phonetic_changes,
                "natural_pause_ms": result.natural_pause_ms,
                "variant_log": result.variant_log,
                # Human-readable summary for logs
                "what_changed": ", ".join(applied_steps) if applied_steps else "none",
                "why_changed": (
                    f"overflow {result.predicted_ms - slot_ms}ms"
                    if not result.fits
                    else "adapted to fit" if result.changed else "already fits"
                ),
            }
        )

    elapsed = time.perf_counter() - t0
    meta: dict[str, Any] = {
        "segments": n,
        "changed": changed_count,
        "overflows_remaining": overflows,
        "validation_fails": validation_fails,
        "step_counters": step_counters,
        "natural_pauses": natural_pauses,
        "elapsed_sec": round(elapsed, 3),
        "lang": lang,
        "segment_audit": segment_audit,
    }

    _log_adaptation(app_dir, segments, out, meta, task_id=task_id)
    _log_full_audit(app_dir, segment_audit, task_id=task_id)
    logger.info(
        "[ADA] %d segments: %d adapted, %d still overflow, %.2fs",
        n,
        changed_count,
        overflows,
        elapsed,
    )
    return out, meta


def _log_full_audit(
    app_dir: Path,
    segment_audit: list[dict[str, Any]],
    *,
    task_id: str = "",
) -> None:
    """
    Write the full per-segment audit log (9 fields per ТЗ):
      Original → Translated → Adapted → Prediction Before → Prediction After
      → What changed → Why changed → ms saved → text_sent_to_tts

    Invariant check: adapted_text MUST equal text_sent_to_tts (pre-phonetics).
    """
    try:
        import uuid as _uuid

        log_dir = app_dir / "output" / "dev"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"ada_segment_audit_{_uuid.uuid4().hex[:8]}.log"

        def esc(s: object) -> str:
            return str(s or "").replace("\n", " ").strip()[:400]

        lines = [
            f"=== ADA SEGMENT AUDIT task={task_id} ===",
            "=" * 80,
        ]
        all_ok = True
        changed_rows = 0

        for row in segment_audit:
            adapted = str(row.get("adapted_text") or "")
            tts_text = str(row.get("text_sent_to_tts") or "")
            # text_sent_to_tts may differ from adapted_text only by phonetic substitution
            # (which is intentional). We flag critical mismatches (complete different text).
            is_critical_mismatch = (
                adapted != tts_text
                and not row.get("phonetic_changes")
                and adapted.strip() != tts_text.strip()
            )
            if is_critical_mismatch:
                all_ok = False

            if not row.get("changed"):
                # Brief line for unchanged segments
                lines.append(
                    f"Seg {row.get('index'):>3} | PASS (no changes) | "
                    f"Target: {row.get('slot_ms', 0)}ms | "
                    f"Predicted: {row.get('predicted_ms_after', row.get('predicted_ms_before', 0))}ms"
                )
                continue

            changed_rows += 1
            idx = row.get("index", "?")
            slot_ms = row.get("slot_ms", 0)
            pred_before = row.get("predicted_ms_before", 0)
            pred_after = row.get("predicted_ms_after", 0)
            saved = row.get("saved_ms", max(0, pred_before - pred_after))
            what = (
                row.get("what_changed")
                or ", ".join(row.get("steps_applied") or [])
                or "none"
            )
            why = row.get("why_changed") or "unknown"
            phonetic = row.get("phonetic_changes") or []
            fits_str = "YES" if row.get("fits") else "NO"
            val_ok_str = "YES" if row.get("validation_ok", True) else "NO"

            lines.append(f"")
            lines.append(
                f"Seg {idx:>3} | Changed | Fits: {fits_str} | Valid: {val_ok_str}"
            )
            lines.append(f"  Target:           {slot_ms}ms")
            lines.append(f"  Prediction BEFORE: {pred_before}ms")
            lines.append(f"  Prediction AFTER:  {pred_after}ms")
            lines.append(f"  Saved:            +{saved}ms")
            lines.append(f"  What changed:     {what}")
            lines.append(f"  Why changed:      {why}")
            if phonetic:
                lines.append(f"  Phonetic fixes:   {', '.join(phonetic)}")
            lines.append(f"  Original text:    {esc(row.get('original_text'))}")
            lines.append(f"  Translated text:  {esc(row.get('translated_text'))}")
            lines.append(f"  Adapted text:     {esc(adapted)}")
            lines.append(f"  Sent to TTS:      {esc(tts_text)}")
            if row.get("validation_notes"):
                lines.append(f"  Validation notes: {row.get('validation_notes')}")
            if is_critical_mismatch:
                lines.append(f"  !!! CRITICAL: adapted_text != text_sent_to_tts !!!")

        lines.append("")
        lines.append("=" * 80)
        lines.append(
            f"SUMMARY: {len(segment_audit)} segments | "
            f"{changed_rows} changed | "
            f"{'ALL_OK' if all_ok else 'HAS_CRITICAL_MISMATCHES'}"
        )
        text_content = "\n".join(lines) + "\n"
        path.write_text(text_content, encoding="utf-8")
        latest = log_dir / "ada_segment_audit_latest.log"
        latest.write_text(text_content, encoding="utf-8")
        if not all_ok:
            logger.error("[ADA] CRITICAL: adapted_text != text_sent_to_tts in %s", path)
        logger.info(
            "[ADA] Segment audit written: %s (%d rows)", latest, len(segment_audit)
        )
    except Exception as exc:
        logger.debug("[ADA] audit log write failed: %s", exc)


def _log_adaptation(
    app_dir: Path,
    original: list[str],
    adapted: list[str],
    meta: dict[str, Any],
    *,
    task_id: str = "",
) -> None:
    try:
        import uuid as _uuid

        log_dir = app_dir / "output" / "dev"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"adaptive_dubbing_adapter_{_uuid.uuid4().hex[:8]}.log"
        lines = [
            f"=== ADAPTIVE DUBBING ADAPTER task={task_id} ===",
            f"segments={meta.get('segments')} changed={meta.get('changed')} "
            f"overflows={meta.get('overflows_remaining')} "
            f"elapsed={meta.get('elapsed_sec')}s",
            f"steps={meta.get('step_counters')}",
            "",
        ]
        for i, (orig, adpt) in enumerate(zip(original[:300], adapted[:300])):
            if orig != adpt:
                lines.append(f"{i}\tORIG: {(orig or '')[:200]}")
                lines.append(f"{i}\tADPT: {(adpt or '')[:200]}")
                lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        latest = log_dir / "adaptive_dubbing_adapter_latest.log"
        latest.write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        logger.debug("[ADA] log write failed: %s", exc)
