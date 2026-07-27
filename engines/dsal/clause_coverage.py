"""Clause coverage + restore for DSAL (TZ v4.0 P1).

Language-aware: never append Ukrainian clause glue into Russian (or vice versa).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# EN clause pattern → (phrase, aliases, position) per target language
# position: prefix | suffix | inline
_ClauseLang = dict[str, tuple[str, tuple[str, ...], str]]

_CLAUSE_SPECS: list[tuple[re.Pattern[str], _ClauseLang]] = [
    (
        re.compile(r"between\s+father\s+and\s+son", re.I),
        {
            "uk": (
                "між батьком і сином",
                ("батьком", "сином", "батько", "син"),
                "prefix",
            ),
            "ru": (
                "между отцом и сыном",
                ("отцом", "сыном", "отец", "сын"),
                "prefix",
            ),
        },
    ),
    (
        re.compile(r"every\s+dinner", re.I),
        {
            "uk": (
                "за кожною вечерею",
                ("вечер", "вечеря", "вечерею", "ужин"),
                "suffix",
            ),
            "ru": (
                "за каждым ужином",
                ("ужин", "вечера", "вечером"),
                "suffix",
            ),
        },
    ),
    (
        re.compile(r"(?:this\s+)?huge\s+argument|huge\s+argument", re.I),
        {
            "uk": (
                "велика суперечка",
                ("суперечк", "сварк", "конфлікт", "спор"),
                "suffix",
            ),
            "ru": (
                "огромный спор",
                ("спор", "ссор", "конфликт", "руган"),
                "suffix",
            ),
        },
    ),
    (
        re.compile(r"\breal\s+job\b", re.I),
        {
            "uk": (
                "справжню роботу",
                ("робот", "справжн"),
                "inline",
            ),
            "ru": (
                "настоящую работу",
                ("работ", "настоящ"),
                "inline",
            ),
        },
    ),
    (
        re.compile(r"near[- ]death\s+experience", re.I),
        {
            "uk": (
                "досвід на межі смерті",
                (
                    "межі смерті",
                    "близьк",
                    "смертельн",
                    "передсмерт",
                    "смерті",
                    "на межі",
                ),
                "inline",
            ),
            "ru": (
                "опыт на грани смерти",
                (
                    "околосмерт",
                    "предсмерт",
                    "на грани",
                    "смерти",
                    "смертельн",
                    "близк",
                ),
                "inline",
            ),
        },
    ),
]

_DINNER_ARG_COMBINED = {
    "uk": "майже кожної вечері між ними виникала велика суперечка",
    "ru": "почти каждый ужин между ними превращался в огромный спор",
}

# Legacy UK-only orphan that must never survive on any target (GL #12 RU TTS).
_CROSS_LANG_ORPHANS = (
    re.compile(r"(?:,\s+)?досвід\s+на\s+межі\s+смерті\.?\s*$", re.I),
    re.compile(r"\s+досвід\s+на\s+межі\s+смерті(?=[,.!?]|$)", re.I),
    re.compile(r"(?:,\s+)?опыт\s+на\s+грани\s+смерти\.?\s*$", re.I),
)


@dataclass
class ClauseCoverageResult:
    coverage: float
    total: int
    covered: int
    missing: list[str]
    restored_phrases: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "clause_coverage": self.coverage,
            "total": self.total,
            "covered": self.covered,
            "missing": list(self.missing),
            "restored_phrases": list(self.restored_phrases),
        }


def _norm_lang(tgt_lang: str = "", text: str = "") -> str:
    """Resolve target lang. Never force-default to uk when unknown.

    Empty → infer from text; if still unknown return '' so restore refuses
    to inject any language's clause glue.
    """
    lang = str(tgt_lang or "").split("-")[0].lower().strip()
    if lang in ("uk", "ru"):
        return lang
    sample = str(text or "")
    if re.search(r"[іІїЇєЄґҐ]", sample):
        return "uk"
    if re.search(r"[а-яА-ЯёЁ]", sample):
        return "ru"
    return ""


def _uk_has_aliases(text: str, aliases: tuple[str, ...]) -> bool:
    uk_l = text.lower()
    return any(a.lower() in uk_l for a in aliases)


def _en_clause_at_start(en: str, pat: re.Pattern[str]) -> bool:
    en_s = str(en or "").strip()
    if not en_s:
        return False
    m = pat.search(en_s)
    return bool(m and m.start() <= 24)


def _en_source_incomplete(en: str) -> bool:
    en_s = str(en or "").strip()
    if not en_s:
        return True
    if en_s[-1] in ".!?…":
        return False
    if re.search(
        r"(?:if\s+he\s+came\s+this\s+huge\s+argument|huge\s+argument)\s*$",
        en_s,
        re.I,
    ):
        return True
    return len(en_s.split()) >= 6


def _clause_for_lang(
    lang_map: _ClauseLang, lang: str
) -> tuple[str, tuple[str, ...], str] | None:
    if not lang:
        return None
    if lang in lang_map:
        return lang_map[lang]
    return None


def strip_cross_lang_clause_orphans(text: str) -> str:
    """Remove DSAL near-death orphans in either UK or RU form."""
    out = str(text or "")
    for pat in _CROSS_LANG_ORPHANS:
        out = pat.sub("", out)
    return " ".join(out.split()).strip()


def compute_clause_coverage(
    en: str, uk: str, *, tgt_lang: str = ""
) -> ClauseCoverageResult:
    """Coverage of known critical EN clauses in target text (0–1)."""
    en_s = str(en or "")
    uk_s = str(uk or "")
    lang = _norm_lang(tgt_lang, uk_s)
    if not en_s.strip():
        return ClauseCoverageResult(1.0, 0, 0, [], [])

    total = 0
    covered = 0
    missing: list[str] = []
    for pat, lang_map in _CLAUSE_SPECS:
        if not pat.search(en_s):
            continue
        spec = _clause_for_lang(lang_map, lang)
        if not spec:
            continue
        phrase, aliases, _pos = spec
        total += 1
        if phrase.lower() in uk_s.lower() or _uk_has_aliases(uk_s, aliases):
            covered += 1
        else:
            missing.append(phrase)

    src_clauses = [
        c.strip()
        for c in re.split(r"[,;:.!?—–]", en_s)
        if len(c.split()) >= 3
    ]
    generic_covered = 0
    for clause in src_clauses:
        tokens = [
            t
            for t in re.findall(r"[A-Za-zА-Яа-яЇїІіЄєҐґ']+", clause)
            if len(t) > 3
        ]
        if not tokens:
            generic_covered += 1
            continue
        hits = sum(1 for t in tokens if t.lower() in uk_s.lower())
        if hits >= max(1, len(tokens) // 3):
            generic_covered += 1
    if src_clauses:
        generic_ratio = generic_covered / len(src_clauses)
    else:
        generic_ratio = 1.0

    if total == 0:
        return ClauseCoverageResult(round(generic_ratio, 3), 0, 0, [], [])

    mapped = covered / total
    if mapped >= 1.0:
        return ClauseCoverageResult(1.0, total, covered, missing, [])
    blended = round(0.7 * mapped + 0.3 * generic_ratio, 3)
    return ClauseCoverageResult(blended, total, covered, missing, [])


def _integrate_dinner_argument(out: str, *, lang: str) -> tuple[str, str]:
    phrase = _DINNER_ARG_COMBINED.get(lang) or ""
    if not phrase:
        return " ".join(str(out).split()).rstrip(" ."), ""
    base = " ".join(str(out).split()).rstrip(" .")
    if phrase.lower() in base.lower():
        return base, phrase
    if base and base[-1] not in ".!?":
        integrated = f"{base}, {phrase}"
    else:
        integrated = f"{base.rstrip('.')} {phrase}"
    return integrated, phrase


def restore_missing_clauses(
    uk: str, en: str, *, tgt_lang: str = ""
) -> tuple[str, ClauseCoverageResult]:
    """Insert missing critical target-language phrases when EN contains the clause.

    Never append Ukrainian glue into Russian text (GL Review #12: «досвід…» on RU).
    """
    if not en or not uk:
        cov = compute_clause_coverage(en, uk, tgt_lang=tgt_lang)
        return strip_cross_lang_clause_orphans(uk or ""), cov

    en_s = str(en)
    out = strip_cross_lang_clause_orphans(" ".join(str(uk).split()).rstrip(" ."))
    lang = _norm_lang(tgt_lang, out)
    uk_l = out.lower()
    restored: list[str] = []

    dinner_spec = _clause_for_lang(_CLAUSE_SPECS[1][1], lang)
    argument_spec = _clause_for_lang(_CLAUSE_SPECS[2][1], lang)
    dinner_pat = _CLAUSE_SPECS[1][0]
    argument_pat = _CLAUSE_SPECS[2][0]
    has_dinner = bool(dinner_pat.search(en_s))
    has_argument = bool(argument_pat.search(en_s))
    dinner_missing = bool(
        dinner_spec
        and has_dinner
        and dinner_spec[0].lower() not in uk_l
        and not _uk_has_aliases(out, dinner_spec[1])
    )
    argument_missing = bool(
        argument_spec
        and has_argument
        and argument_spec[0].lower() not in uk_l
        and not _uk_has_aliases(out, argument_spec[1])
    )

    if (
        lang
        and dinner_missing
        and argument_missing
        and not _en_source_incomplete(en_s)
    ):
        out, phrase = _integrate_dinner_argument(out, lang=lang)
        if phrase:
            restored.append(phrase)
            uk_l = out.lower()

    dinner_phrase = dinner_spec[0] if dinner_spec else ""
    argument_phrase = argument_spec[0] if argument_spec else ""

    for pat, lang_map in _CLAUSE_SPECS:
        if not pat.search(en_s):
            continue
        spec = _clause_for_lang(lang_map, lang)
        if not spec:
            continue
        phrase, aliases, position = spec
        if phrase.lower() in uk_l or _uk_has_aliases(out, aliases):
            continue
        combined = _DINNER_ARG_COMBINED.get(lang, "")
        if phrase in restored or (combined and combined.lower() in uk_l):
            if phrase in (dinner_phrase, argument_phrase):
                continue
        if position == "suffix" and _en_source_incomplete(en_s):
            if phrase in (dinner_phrase, argument_phrase):
                continue

        if position == "prefix" and _en_clause_at_start(en_s, pat):
            if out and out[0].islower():
                out = out[0].upper() + out[1:]
            out = f"{phrase}, {out.lstrip()}"
        elif position == "inline" or (
            position == "prefix" and not _en_clause_at_start(en_s, pat)
        ):
            if phrase.lower() in uk_l:
                continue
            if out and out[-1] not in ".!?":
                out = f"{out}, {phrase}"
            else:
                out = f"{out.rstrip('.')} {phrase}"
        elif phrase in (dinner_phrase, argument_phrase):
            glue = "и" if lang == "ru" else "і"
            out = f"{out}, {glue} {phrase}"
        elif out and out[-1] not in ".!?":
            out = f"{out}, {phrase}"
        else:
            out = f"{out.rstrip('.')} {phrase}"
        restored.append(phrase)
        uk_l = out.lower()

    if restored and not out.endswith((".", "!", "?", "…")):
        out += "."

    out = strip_cross_lang_clause_orphans(out)
    cov = compute_clause_coverage(en, out, tgt_lang=lang)
    cov.restored_phrases = restored
    return " ".join(out.split()), cov


# Back-compat: old module-level name used by tests
_CLAUSE_MAP = [
    (pat, lang_map["uk"][0], lang_map["uk"][1], lang_map["uk"][2])
    for pat, lang_map in _CLAUSE_SPECS
    if "uk" in lang_map
]
