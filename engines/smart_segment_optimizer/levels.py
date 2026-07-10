"""Multi-level text shortening for Smart Segment Optimizer V2."""

from __future__ import annotations

import re

# Level 2 — long constructions → short (EN + RU + UK).
_SHORTEN_PATTERNS: list[tuple[str, str]] = [
    # ── English ──────────────────────────────────────────────────────────────
    (r"\bin order to\b", "to"),
    (r"\bat the present time\b", "now"),
    (r"\bat this moment in time\b", "now"),
    (r"\bat this moment\b", "now"),
    (r"\bit is necessary to\b", "must"),
    (r"\bit should be noted that?\s*", ""),
    (r"\bI think that\b", "I think"),
    (r"\bI believe that\b", "I believe"),
    (r"\bhowever,\s*", "but "),
    (r"\bnevertheless,\s*", "but "),
    (r"\bin addition,\s*", "also "),
    (r"\bdue to the fact that\b", "because"),
    (r"\bfor the purpose of\b", "to"),
    (r"\bin spite of the fact that\b", "although"),
    (r"\bnotwithstanding the fact that\b", "although"),
    (r"\bwith the exception of\b", "except"),
    (r"\bwith regard to\b", "about"),
    (r"\bwith respect to\b", "about"),
    (r"\bat this point in time\b", "now"),
    (r"\bin the event that\b", "if"),
    (r"\bon the occasion of\b", "when"),
    (r"\bhas the ability to\b", "can"),
    (r"\bis able to\b", "can"),
    (r"\bmake a decision\b", "decide"),
    (r"\bcome to a conclusion\b", "conclude"),
    # ── Russian ──────────────────────────────────────────────────────────────
    (r"\bв настоящее время\b", "сейчас"),
    (r"\bв данный момент\b", "сейчас"),
    (r"\bв данное время\b", "сейчас"),
    (r"\bнеобходимо\b", "нужно"),
    (r"\bследует отметить,?\s*", ""),
    (r"\bстоит отметить,?\s*", ""),
    (r"\bнужно сказать,?\s*", ""),
    (r"\bя думаю, что\b", "думаю,"),
    (r"\bмне кажется, что\b", "кажется,"),
    (r"\bтем не менее,?\s*", "но "),
    (r"\bоднако,?\s*", "но "),
    (r"\bв связи с тем, что\b", "так как"),
    (r"\bв связи с этим\b", "поэтому"),
    (r"\bс учётом того, что\b", "учитывая"),
    (r"\bнесмотря на то, что\b", "хотя"),
    (r"\bза исключением\b", "кроме"),
    (r"\bпринимая во внимание\b", "учитывая"),
    (r"\bявляется\b", "—"),
    (r"\bявляются\b", "—"),
    (r"\bв том числе\b", "включая"),
    (r"\bна протяжении долгого времени\b", "долго"),
    (r"\bна протяжении длительного времени\b", "долго"),
    (r"\bпо причине того, что\b", "потому что"),
    (r"\bиз-за того, что\b", "потому что"),
    (r"\bпо той причине, что\b", "потому что"),
    (r"\bнесмотря на это,?\s*", "но "),
    (r"\bтаким образом,?\s*", ""),
    (r"\bследовательно,?\s*", "значит, "),
    (r"\bсоответственно,?\s*", ""),
    (r"\bсо своей стороны,?\s*", ""),
    (r"\bв первую очередь\b", "прежде всего"),
    (r"\bпрежде всего\b", "сначала"),
    (r"\bпо всей видимости,?\s*", "видимо, "),
    (r"\bпо всей вероятности,?\s*", "видимо, "),
    # ── Ukrainian ────────────────────────────────────────────────────────────
    (r"\bна даний момент\b", "зараз"),
    (r"\bна даний час\b", "зараз"),
    (r"\bв даний час\b", "зараз"),
    (r"\bнезважаючи на те, що\b", "хоча"),
    (r"\bнезважаючи на це,?\s*", "але "),
    (r"\bз огляду на те, що\b", "оскільки"),
    (r"\bз огляду на це\b", "тому"),
    (r"\bбереться до уваги\b", "враховується"),
    (r"\bвраховуючи те, що\b", "оскільки"),
    (r"\bза винятком\b", "крім"),
    (r"\bнеобхідно зазначити,?\s*", ""),
    (r"\bслід зазначити,?\s*", ""),
    (r"\bтакий чином,?\s*", ""),
    (r"\bвідповідно,?\s*", ""),
    (r"\bдавати можливість\b", "дозволяти"),
    (r"\bдає можливість\b", "дозволяє"),
    (r"\bнадавати можливість\b", "дозволяти"),
    (r"\bнадає можливість\b", "дозволяє"),
    (r"\bпо всій видимості,?\s*", "мабуть, "),
    (r"\bпо всій вірогідності,?\s*", "мабуть, "),
    (r"\bперш за все\b", "спочатку"),
    (r"\bнасамперед\b", "спочатку"),
]

_SYNONYMS: dict[str, str] = {
    # English
    "approximately": "about",
    "immediately": "at once",
    "utilize": "use",
    "demonstrate": "show",
    "subsequently": "then",
    "additionally": "also",
    "consequently": "so",
    "nevertheless": "but",
    "furthermore": "also",
    "nonetheless": "still",
    # Russian
    "приблизительно": "примерно",
    "незамедлительно": "сразу",
    "продемонстрировать": "показать",
    "использовать": "применить",
    "осуществить": "сделать",
    "осуществлять": "делать",
    "предоставить": "дать",
    "предоставлять": "давать",
    "достаточно": "немного",
    "зараз": "тепер",
    "потому что": "бо",
    "тому що": "бо",
    # Ukrainian
    "приблизно": "близько",
    "продемонструвати": "показати",
    "використовувати": "застосовувати",
    "здійснити": "зробити",
    "здійснювати": "робити",
}

_SOFTENERS = re.compile(
    r"\b(very|really|quite|rather|somewhat|fairly|extremely|"
    r"очень|слишком|довольно|достаточно|немного|весьма|"
    r"dużo|bardzo|sehr|ziemlich|très|vraiment|muy|realmente|"
    r"дуже|надто|досить|цілком)\s+",
    re.IGNORECASE,
)

# Level 5 — drop subordinate/parenthetical clauses (safe: preserve main clause).
# Patterns below target secondary information that can be dropped without changing
# the key message. Applied only when levels 1-4 are insufficient.

# Introductory phrases that add nothing to the dub meaning
_INTRO_STRIP = re.compile(
    r"^(В результаті,|У результаті,|Загалом,|В общем,|В целом,|"
    r"По суті,|По сути,|Таким чином,|Таким образом,|"
    r"Очевидно,|Зрозуміло,|Ясно,|Звичайно,|Конечно,|"
    r"Як не дивно,|Как ни странно,|Примечательно,|Примітно,)\s*",
    re.IGNORECASE,
)

# Apposition blocks: "Name, who is X," → "Name"
_APPOSITION_STRIP = re.compile(
    r",\s+(який|яка|яке|які|который|которая|которое|которые|who|that|which)\s+[^,]{4,40},",
    re.IGNORECASE,
)

# Trailing optional clarification after em-dash when main clause is already complete
_TRAILING_DASH_STRIP = re.compile(
    r"\s+[—–]\s+[^.!?]{6,60}$",
)

# "so X that Y" → "very X" (drop "that Y" clause)
_SO_THAT_STRIP = re.compile(
    r"\b(такой|такого|таку|так|so)\s+([а-яА-ЯіІїЇєЄa-zA-Z'-]+),?\s+"
    r"(что|щоб|що|that)\s+[^.!?,]{6,50}",
    re.IGNORECASE,
)

_LEVEL_NAMES = {
    1: "contextual_fillers",
    2: "short_constructions",
    3: "synonyms",
    4: "secondary_details",
    5: "subordinate_clauses",
}


def level_name(level: int) -> str:
    return _LEVEL_NAMES.get(level, f"level_{level}")


def apply_level(text: str, level: int) -> tuple[str, str]:
    """Apply one optimization level (levels 2–4); level 1 handled incrementally in optimizer."""
    out = " ".join(str(text or "").split())
    if not out:
        return out, "empty"

    if level == 1:
        return out, "use iter_filler_removals"

    if level == 2:
        changed = out
        for pattern, repl in _SHORTEN_PATTERNS:
            changed = re.sub(pattern, repl, changed, flags=re.IGNORECASE)
        changed = " ".join(changed.split())
        if changed != out:
            return changed, "replaced long constructions with shorter forms"
        return out, "no long constructions found"

    if level == 3:
        changed = _SOFTENERS.sub("", out)
        words = changed.split()
        new_words: list[str] = []
        replaced = False
        for w in words:
            low = w.lower().strip(".,!?;:")
            rep = _SYNONYMS.get(low)
            if rep is not None:
                if rep:
                    new_words.append(rep)
                replaced = True
            else:
                new_words.append(w)
        changed = " ".join(new_words)
        changed = " ".join(changed.split())
        if changed != out:
            return changed, "replaced long words with shorter natural synonyms"
        return out, "no synonym replacements applied"

    if level == 4:
        changed = re.sub(r"\([^)]{0,120}\)", "", out)
        changed = re.sub(r"\s—\s[^.!?]{10,}", "", changed)
        changed = re.sub(r",\s*[^,]{8,},\s*", ", ", changed)
        changed = " ".join(changed.split())
        if changed != out:
            return changed, "removed secondary parenthetical or clause details"
        return out, "no secondary details removed"

    if level == 5:
        changed = _INTRO_STRIP.sub("", out)
        changed = _APPOSITION_STRIP.sub(",", changed)
        changed = _TRAILING_DASH_STRIP.sub("", changed)
        changed = _SO_THAT_STRIP.sub(r"очень \2", changed)
        changed = " ".join(changed.split())
        # Require the result stays semantically viable (>= 60% of original length)
        if changed and len(changed) >= len(out) * 0.55 and changed != out:
            return changed, "removed subordinate clause or apposition"
        return out, "no safe subordinate clause removal"

    return out, "unknown level"
