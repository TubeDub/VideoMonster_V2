"""Ukrainian display forms for protected names (broadcast dub style)."""

from __future__ import annotations

import re

GEORGE_JR_UK_NOM = "Джордж-молодший"
GEORGE_JR_UK_GEN = "Джорджа-молодшого"
GEORGE_LUCAS_UK = "Джордж Лукас"
STAR_WARS_UK = "«Зоряні війни»"
USC_UK_NOM = "Університет Південної Каліфорнії"
USC_UK_GEN = "Університету Південної Каліфорнії"
USC_UK_DAT = "до Університету Південної Каліфорнії"


def george_jr_target_form(*, original: str, tgt_lang: str = "uk") -> str:
    lang = (tgt_lang or "uk").split("-")[0].lower()
    if lang != "uk":
        return "George Jr."
    src = str(original or "")
    if re.search(r"(?<!\w)George\s+Jr\.?(?!\w)", src, re.I):
        # Genitive after "що", "в", "у" (people at), possessive
        if re.search(
            r"(?<!\w)(?:що|в|у|з|до|від|про|для|після|перед|між)\s+George\s+Jr\.?(?!\w)",
            src,
            re.I,
        ):
            return GEORGE_JR_UK_GEN
        return GEORGE_JR_UK_NOM
    return GEORGE_JR_UK_NOM


def normalize_george_jr_uk(text: str) -> str:
    """Unify spaced / Latin Jr. forms to hyphenated Ukrainian."""
    out = str(text or "")
    if not out.strip():
        return out

    out = re.sub(r"\bGeorge\s+Jr\.?\b", GEORGE_JR_UK_NOM, out, flags=re.I)
    out = re.sub(r"\bДжордж\s+молодший\b", GEORGE_JR_UK_NOM, out, flags=re.I)
    out = re.sub(r"\bДжорджа\s+молодшого\b", GEORGE_JR_UK_GEN, out, flags=re.I)
    out = re.sub(r"\bДжорджу\s+молодшому\b", "Джорджу-молодшому", out, flags=re.I)
    out = re.sub(
        r"\b" + re.escape(GEORGE_JR_UK_NOM) + r"\s+вилетіл[ао]\b",
        f"{GEORGE_JR_UK_GEN} викинуло",
        out,
        flags=re.I,
    )
    return out.strip()


def normalize_usc_uk(text: str, *, original: str = "") -> str:
    """Expand USC to full university name in Ukrainian dub."""
    out = str(text or "")
    src = str(original or "")
    if not re.search(r"\bUSC\b", src, re.I) and "Southern California" not in src:
        return out

    out = re.sub(
        r"\b(?:у|в)\s+USC\b",
        f"з {USC_UK_GEN}",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bзвернувся\s+до\s+USC\b",
        f"подав заявку {USC_UK_DAT}",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bдо\s+USC\b",
        USC_UK_DAT,
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bUSC(?:'s)?\s+film\s+school\b",
        f"кіношколи {USC_UK_GEN}",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bлист\s+про\s+зарахування\s+USC\b",
        f"лист про зарахування з {USC_UK_GEN}",
        out,
        flags=re.I,
    )
    out = re.sub(r"\bUSC\b", USC_UK_NOM, out)
    return out.strip()


def normalize_usc_uk_phrase_fixes(text: str) -> str:
    """Fix common USC calques after full-name expansion."""
    out = str(text or "")
    out = re.sub(
        r"\bя\s+знаю\s+людей\s+Університет\s+Південної\s+Каліфорнії\b",
        f"я знаю людей у {USC_UK_GEN}",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bОтриманий\s+лист\s+від\s+Університет\s+Південної\s+Каліфорнії\b",
        f"отримав лист про зарахування з {USC_UK_GEN}",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bОтриманий\s+лист\b",
        "отримав лист про зарахування",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bв\s+їхній\s+програма\s+кіно\b",
        "до їхньої програми з кінематографії",
        out,
        flags=re.I,
    )
    return out.strip()


def collapse_uk_title_quotes(text: str) -> str:
    """Collapse repeated «» around titles (idempotent prep before wrapping)."""
    out = str(text or "")
    if not out.strip():
        return out
    out = re.sub(r"«{2,}", "«", out)
    out = re.sub(r"»{2,}", "»", out)
    out = re.sub(
        r"«+\s*(Зоряні\s+війни)\s*»+",
        STAR_WARS_UK,
        out,
        flags=re.IGNORECASE,
    )
    return out.strip()


def normalize_star_wars_uk(text: str) -> str:
    out = collapse_uk_title_quotes(text)
    if re.search(r"«\s*Зоряні\s+війни\s*»", out, re.IGNORECASE):
        return out
    out = re.sub(
        r"(?<![«»])\bЗоряні\s+війни\b(?![«»])",
        STAR_WARS_UK,
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"\bStar\s+Wars\b", STAR_WARS_UK, out, flags=re.I)
    return collapse_uk_title_quotes(out)


def apply_uk_dub_name_polish(text: str, *, original: str = "") -> str:
    out = collapse_uk_title_quotes(text)
    out = normalize_george_jr_uk(out)
    out = re.sub(
        r"\b(?:в|у)\s+житті\s+Джордж-молодший\b",
        "у житті Джорджа-молодшого",
        out,
        flags=re.I,
    )
    out = re.sub(r"\bжиття\s+Георга\b", "життя Джорджа", out, flags=re.I)
    out = normalize_usc_uk(out, original=original)
    out = normalize_usc_uk_phrase_fixes(out)
    out = normalize_star_wars_uk(out)
    out = re.sub(r"\bGeorge\s+Lucas\b", GEORGE_LUCAS_UK, out, flags=re.I)
    return out.strip()
