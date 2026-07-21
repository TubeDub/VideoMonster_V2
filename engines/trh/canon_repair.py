"""TRH canon entity/phrase repair — glossary-driven + temporary MT garbage cleanup.

TODO: each regex below should migrate to Pre-MT mask/restore when stable.
"""

from __future__ import annotations

import re
from typing import Any


# Longest-first repairs. Ticket column = TODO to move into mask/restore.
_CANON_REPAIRS: list[tuple[re.Pattern[str], str, str]] = [
    # Jr. breakage variants seen in live Review
    (re.compile(r"\bГеоргій\s+Жр\.?", re.I), "Джордж-молодший", "TODO:mask George Jr."),
    (re.compile(r"\bГеорга\s+Жр\.?", re.I), "Джорджа-молодшого", "TODO:mask George Jr."),
    (re.compile(r"\bГеоргій\s+молодш(?:ий|ого)?", re.I), "Джордж-молодший", "TODO:mask George Jr."),
    (re.compile(r"\bГеорга\s+молодш(?:ий|ого)?", re.I), "Джорджа-молодшого", "TODO:mask George Jr."),
    (re.compile(r"\bЮра\s+Джера\b", re.I), "Джорджа-молодшого", "TODO:mask George Jr."),
    (re.compile(r"\bДжордж(?:а|у)?\s+Джер\.?", re.I), "Джордж-молодший", "TODO:mask George Jr."),
    (re.compile(r"\bДжордж(?:а|у)?\s+Джр\.?", re.I), "Джордж-молодший", "TODO:mask George Jr."),
    (re.compile(r"\bДжордж(?:а|у)?\s+Жр\.?", re.I), "Джордж-молодший", "TODO:mask George Jr."),
    (re.compile(r"\bДжордж(?:а|у)?\s+Єр\.?", re.I), "Джордж-молодший", "TODO:mask George Jr."),
    (re.compile(r"\bДжордж(?:а|у)?\s+молодший(?!-)", re.I), "Джордж-молодший", "TODO:canon hyphen"),
    # Standalone broken tokens
    (re.compile(r"\bЖр\.?\b"), "молодший", "TODO:mask Jr"),
    (re.compile(r"\bДжер\.?\b"), "Джордж", "TODO:mask Jr"),
    (re.compile(r"\bДжр\.?\b"), "молодший", "TODO:mask Jr"),
    (re.compile(r"\bЄр\.?\b"), "молодший", "TODO:mask Jr"),
    (re.compile(r"\bДад\b"), "батько", "TODO:glossary dad"),
    # Phrases
    (re.compile(r"\bім\.?\s*Георга\b", re.I), "на ім'я Джордж", "TODO:mask George"),
    (re.compile(r"\bрідн(?:ий|ого|ому)\s+міст\b", re.I), "рідне місто", "TODO:glossary hometown"),
    (re.compile(r"(?<![а-яіїєґА-ЯІЇЄҐ])міст(?![а-яіїєґоО])"), "рідне місто", "TODO:glossary hometown"),
    (re.compile(r"\bгончарн(?:ий|ого|ому|ій|і)\s+трек\w*", re.I), "гоночний трек", "TODO:glossary race track"),
    (re.compile(r"\bстаціонарн(?:ий|ого|ому|ій)\s+комплекс\w*", re.I), "відділення інтенсивної терапії", "TODO:glossary ICU"),
    (re.compile(r"\bбув\s+водінням\b", re.I), "їхав", "TODO:calque driving"),
    (re.compile(r"\bбула\s+водінням\b", re.I), "їхала", "TODO:calque driving"),
    (re.compile(r"\bяк\s+він\s+був\s+водінням\b", re.I), "коли він їхав", "TODO:calque driving"),
    (re.compile(r"\bзірвати\s+війни\b", re.I), "«Зоряні війни»", "TODO:glossary Star Wars"),
    (re.compile(r"\bбудуть\s+зірвати\s+війни\b", re.I), "стане «Зоряними війнами»", "TODO:glossary Star Wars"),
    # English leaks common in this run
    (re.compile(r"\bdreading\b", re.I), "зі страхом очікував", "TODO:en_leak dreading"),
    (re.compile(r"\bobsession\b", re.I), "одержимістю", "TODO:en_leak"),
    (re.compile(r"\bfocus\b", re.I), "зосередженість", "TODO:en_leak"),
    # Residuals after partial Jr/hometown cosmetic fixes
    (re.compile(r"\bна\s+ім['']я\s+Джорджа-молодшого\b", re.I), "на ім'я Джордж-молодший", "TODO:case на ім'я"),
    (re.compile(r"\bавтомобіль,\s*яка\b", re.I), "автомобіль, який", "TODO:gender agreement"),
    (re.compile(r"\bрозім['']ятити(?:\s+в\s+машину)?\b", re.I), "врізалася в машину", "TODO:calque smash"),
    (re.compile(r"\bбув\s+повністю\s+одужав\b", re.I), "повністю одужав", "TODO:double past"),
    (re.compile(r"\bправий\s+мав\s+рацію\b", re.I), "правий", "TODO:synonym double"),
    (re.compile(r"\bавтомобіль\s+на\s+великій\s+швидкості\s+промчала\b", re.I),
     "автомобіль на великій швидкості промчав", "TODO:gender verb"),
    (re.compile(r"\bназивається\s+Fiat\b", re.I), "називається Фіат", "TODO:glossary Fiat"),
]


def apply_canon_repair(
    text: str,
    *,
    original: str = "",
    tgt_lang: str = "uk",
    app_dir: Any = None,
) -> tuple[str, list[str]]:
    """Apply glossary canonical forms + temporary MT garbage repairs."""
    out = str(text or "")
    applied: list[str] = []
    if not out.strip():
        return out, applied

    # Glossary-driven canonical substitutions when source mentions the entity
    try:
        from engines.project_glossary import load_project_glossary

        gloss = load_project_glossary(app_dir=app_dir)
        src_l = str(original or "").lower()
        for entry in gloss.entries:
            labels = [entry.source] + list(entry.aliases or [])
            if not any(lab.lower() in src_l for lab in labels if lab):
                continue
            # If none of acceptable surfaces present — inject/fix broken forms later via regex
            if gloss.is_acceptable(entry.source, out):
                continue
            # USC must not become США (even if another USC token already exists)
            if entry.source.upper() in ("USC", "U.S.C.") or "southern california" in entry.source.lower():
                if re.search(r"\bСША\b", out):
                    fixed = out
                    fixed = re.sub(r"\bлюдей\s+(?:у|в)\s+США\b", "людей в USC", fixed, flags=re.I)
                    fixed = re.sub(r"\b(?:у|в)\s+США\b", "в USC", fixed, flags=re.I)
                    fixed = re.sub(r"\bкіношколи\s+США\b", "кіношколи USC", fixed, flags=re.I)
                    fixed = re.sub(r"\bз\s+кіношколи\s+США\b", "з кіношколи USC", fixed, flags=re.I)
                    fixed = re.sub(r"\bдо\s+США\b", "до USC", fixed, flags=re.I)
                    if fixed != out:
                        out = fixed
                        applied.append(f"glossary:{entry.source}->USC")
                    elif "USC" not in out:
                        out = re.sub(r"\bСША\b", entry.canonical or "USC", out, count=1)
                        applied.append(f"glossary:{entry.source}->USC")
    except Exception:
        pass

    for pat, repl, ticket in _CANON_REPAIRS:
        if pat.search(out):
            new = pat.sub(repl, out)
            if new != out:
                out = new
                applied.append(ticket)

    # Normalize «Джордж молодший» → hyphen form
    out2 = re.sub(
        r"\bДжордж(?:а|у)?\s+молодш(?:ий|ого|ому)\b",
        lambda m: "Джордж-молодший"
        if m.group(0).startswith("Джордж ")
        else (
            "Джорджа-молодшого"
            if "Джорджа" in m.group(0)
            else "Джорджу-молодшому"
        ),
        out,
    )
    if out2 != out:
        out = out2
        applied.append("TODO:canon hyphen Jr")

    # Collapse double «рідне рідне місто»
    out = re.sub(r"(рідне\s+)+місто", "рідне місто", out, flags=re.I)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out, applied


def still_broken_entities(text: str, original: str = "") -> list[str]:
    """Return remaining critical breakage tokens (for Fast QA)."""
    tr = str(text or "")
    bad: list[str] = []
    checks = [
        (r"\bЖр\.?\b", "Жр"),
        (r"\bДжер\.?\b", "Джер"),
        (r"\bДжр\.?\b", "Джр"),
        (r"\bЄр\.?\b", "Єр"),
        (r"\bГеоргій\b", "Георгій"),
        (r"\bгончар", "гончарний"),
        (r"\bстаціонарн\w*\s+комплекс", "стаціонарний комплекс"),
        (r"\bdreading\b", "dreading"),
        (r"\bзірвати\s+війни\b", "зірвати війни"),
        (r"\bбув\s+водінням\b", "був водінням"),
    ]
    for pat, label in checks:
        if re.search(pat, tr, re.I):
            bad.append(label)
    if re.search(r"\bUSC\b", original or "", re.I) and re.search(r"\bСША\b", tr):
        bad.append("USC→США")
    if re.search(r"\bhometown\b", original or "", re.I):
        if re.search(r"(?<![а-яіїєґ])міст(?![а-яіїєґо])", tr, re.I) and "рідне місто" not in tr.lower():
            bad.append("hometown→міст")
    return bad
