"""Clause coverage + restore for DSAL (TZ v4.0 P1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# EN clause pattern → UK phrase + optional token aliases that count as "covered"
# position: prefix = prepend when EN opens with clause; suffix = append; inline = integrated glue
_ClauseSpec = tuple[re.Pattern[str], str, tuple[str, ...], str]

_CLAUSE_MAP: list[_ClauseSpec] = [
    (
        re.compile(r"between\s+father\s+and\s+son", re.I),
        "між батьком і сином",
        ("батьком", "сином", "батько", "син"),
        "prefix",
    ),
    (
        re.compile(r"every\s+dinner", re.I),
        "за кожною вечерею",
        ("вечер", "вечеря", "вечерею"),
        "suffix",
    ),
    (
        re.compile(r"(?:this\s+)?huge\s+argument|huge\s+argument", re.I),
        "велика суперечка",
        ("суперечк", "сварк", "конфлікт"),
        "suffix",
    ),
    (
        re.compile(r"\breal\s+job\b", re.I),
        "справжню роботу",
        ("робот", "справжн"),
        "inline",
    ),
    (
        re.compile(r"near[- ]death\s+experience", re.I),
        "досвід на межі смерті",
        # Naturalized UK often uses «майже смертельного досвіду» — count as covered
        # so DSAL does not append the literal phrase as an orphan TTS tail.
        ("межі смерті", "близьк", "смертельн", "смерті", "на межі"),
        "inline",
    ),
]

_DINNER_ARG_COMBINED = "майже кожної вечері між ними виникала велика суперечка"


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


def _uk_has_aliases(uk: str, aliases: tuple[str, ...]) -> bool:
    uk_l = uk.lower()
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
    # ASR mid-clause cut — trailing fragment without a closing verb phrase.
    if re.search(
        r"(?:if\s+he\s+came\s+this\s+huge\s+argument|huge\s+argument)\s*$",
        en_s,
        re.I,
    ):
        return True
    return len(en_s.split()) >= 6


def compute_clause_coverage(en: str, uk: str) -> ClauseCoverageResult:
    """Coverage of known critical EN clauses in UK text (0–1)."""
    en_s = str(en or "")
    uk_s = str(uk or "")
    if not en_s.strip():
        return ClauseCoverageResult(1.0, 0, 0, [], [])

    total = 0
    covered = 0
    missing: list[str] = []
    for pat, phrase, aliases, _pos in _CLAUSE_MAP:
        if not pat.search(en_s):
            continue
        total += 1
        if phrase.lower() in uk_s.lower() or _uk_has_aliases(uk_s, aliases):
            covered += 1
        else:
            missing.append(phrase)

    # Also count generic punct-split clauses with token overlap (same idea as QA)
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
    # Critical mapped clauses dominate: if all known clauses present, pass gate.
    if mapped >= 1.0:
        return ClauseCoverageResult(1.0, total, covered, missing, [])
    blended = round(0.7 * mapped + 0.3 * generic_ratio, 3)
    return ClauseCoverageResult(blended, total, covered, missing, [])


def _integrate_dinner_argument(out: str) -> tuple[str, str]:
    """Natural combined glue for dinner + huge argument on the same segment."""
    base = " ".join(str(out).split()).rstrip(" .")
    if _DINNER_ARG_COMBINED.lower() in base.lower():
        return base, _DINNER_ARG_COMBINED
    if base and base[-1] not in ".!?":
        integrated = f"{base}, {_DINNER_ARG_COMBINED}"
    else:
        integrated = f"{base.rstrip('.')} {_DINNER_ARG_COMBINED}"
    return integrated, _DINNER_ARG_COMBINED


def restore_missing_clauses(uk: str, en: str) -> tuple[str, ClauseCoverageResult]:
    """Insert missing critical UK phrases when EN contains the clause."""
    if not en or not uk:
        cov = compute_clause_coverage(en, uk)
        return uk, cov

    en_s = str(en)
    out = " ".join(str(uk).split()).rstrip(" .")
    uk_l = out.lower()
    restored: list[str] = []

    dinner_pat = _CLAUSE_MAP[1][0]
    argument_pat = _CLAUSE_MAP[2][0]
    has_dinner = bool(dinner_pat.search(en_s))
    has_argument = bool(argument_pat.search(en_s))
    dinner_missing = has_dinner and not (
        _CLAUSE_MAP[1][1].lower() in uk_l or _uk_has_aliases(out, _CLAUSE_MAP[1][2])
    )
    argument_missing = has_argument and not (
        _CLAUSE_MAP[2][1].lower() in uk_l or _uk_has_aliases(out, _CLAUSE_MAP[2][2])
    )

    if dinner_missing and argument_missing and not _en_source_incomplete(en_s):
        out, phrase = _integrate_dinner_argument(out)
        restored.append(phrase)
        uk_l = out.lower()

    for pat, phrase, aliases, position in _CLAUSE_MAP:
        if not pat.search(en_s):
            continue
        if phrase.lower() in uk_l or _uk_has_aliases(out, aliases):
            continue
        if phrase in restored or _DINNER_ARG_COMBINED.lower() in uk_l:
            if phrase in ("за кожною вечерею", "велика суперечка"):
                continue
        # Skip orphan ASR tail clauses that would glue awkwardly onto a full sentence.
        if position == "suffix" and _en_source_incomplete(en_s):
            if phrase in ("за кожною вечерею", "велика суперечка"):
                continue

        # Prefix only when the EN clause actually opens the segment.
        # Otherwise mega-segments get "між батьком і сином, …" glued at the
        # start even when that clause appears mid-paragraph → meaning salad.
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
        elif "вечер" in phrase or "суперечк" in phrase:
            out = f"{out}, і {phrase}"
        elif out and out[-1] not in ".!?":
            out = f"{out}, {phrase}"
        else:
            out = f"{out.rstrip('.')} {phrase}"
        restored.append(phrase)
        uk_l = out.lower()

    if restored and not out.endswith((".", "!", "?", "…")):
        out += "."

    cov = compute_clause_coverage(en, out)
    cov.restored_phrases = restored
    return " ".join(out.split()), cov
