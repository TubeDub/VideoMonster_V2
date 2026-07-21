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
    # Impersonal «викинуло» requires genitive patient
    out = re.sub(
        r"\b" + re.escape(GEORGE_JR_UK_NOM) + r"\s+викинул[ао]\b",
        f"{GEORGE_JR_UK_GEN} викинуло",
        out,
        flags=re.I,
    )
    out = re.sub(r"\bДжордж\s+викинул[ао]\b", "Джорджа викинуло", out, flags=re.I)
    return out.strip()


def normalize_usc_uk(text: str, *, original: str = "") -> str:
    """Keep USC short in dub (timing). Collapse accidental full-name expansions."""
    out = str(text or "")
    src = str(original or "")
    src_has_usc = bool(
        re.search(r"\bUSC\b", src, re.I) or "Southern California" in src
    )
    has_usc_surface = bool(
        src_has_usc
        or re.search(r"\bUSC\b", out)
        or re.search(r"Університет\w*\s+Південної\s+Каліфорнії", out, re.I)
    )
    if not has_usc_surface and not (src_has_usc and re.search(r"\bСША\b", out)):
        return out

    # Prefer compact USC — full name eats TTS budget on long narrative beats
    out = re.sub(
        r"\bУніверситет(?:у|ом|і)?\s+Південної\s+Каліфорнії\b",
        "USC",
        out,
        flags=re.I,
    )
    # Marian often maps USC → США; repair whenever EN mentions USC
    if src_has_usc or re.search(r"\bUSC\b", out):
        out = re.sub(r"\bлюдей\s+(?:у|в)\s+США\b", "людей в USC", out, flags=re.I)
        out = re.sub(r"\bкіношколи\s+США\b", "кіношколи USC", out, flags=re.I)
        out = re.sub(r"\bз\s+кіношколи\s+США\b", "з кіношколи USC", out, flags=re.I)
        out = re.sub(r"\bдо\s+США\b", "до USC", out, flags=re.I)
        out = re.sub(r"\b(?:у|в)\s+США\b", "в USC", out, flags=re.I)
    out = re.sub(
        r"\bзвернувся\s+до\s+USC\b",
        "подав заявку до USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bUSC(?:'s)?\s+film\s+school\b",
        "кіношколи USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bлист\s+про\s+зарахування\s+USC\b",
        "лист про зарахування з кіношколи USC",
        out,
        flags=re.I,
    )
    # Keep Latin USC token as-is (do not expand to full Ukrainian name)
    return out.strip()


def normalize_usc_uk_phrase_fixes(text: str) -> str:
    """Fix common USC calques; keep compact USC form."""
    out = str(text or "")
    out = re.sub(
        r"\bкіношколи\s+Університет(?:у)?\s+Південної\s+Каліфорнії\b",
        "кіношколи USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bз\s+кіношколи\s+Університет(?:у)?\s+Південної\s+Каліфорнії\b",
        "з кіношколи USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bотримає\s+лист\s+про\s+зарахування\b",
        "отримав лист про зарахування",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bя\s+знаю\s+людей\s+(?:з\s+)?Університет(?:у)?\s+Південної\s+Каліфорнії\b",
        "я знаю людей в USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bя\s+знаю\s+людей\s+з\s+USC\b",
        "я знаю людей в USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bОтриманий\s+лист\s+від\s+Університет(?:у)?\s+Південної\s+Каліфорнії\b",
        "отримав лист про зарахування з кіношколи USC",
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
    # Collapse duplicated «Південної Каліфорнії»
    out = re.sub(
        r"(Південної\s+Каліфорнії)(?:\s*,\s*Південної\s+Каліфорнії)+",
        r"\1",
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


def normalize_haskell_wexler_uk(text: str, *, original: str = "") -> str:
    """Keep Haskell Wexler in Ukrainian phonetic form (Хаскел Уекслер)."""
    out = str(text or "")
    src = str(original or "")
    if not re.search(r"Haskell|Wexler|Хаскел|Уекслер", src + " " + out, re.I):
        return out
    out = re.sub(r"\bHaskell\s+Wexler\b", "Хаскел Уекслер", out, flags=re.I)
    out = re.sub(r"\bHaskell\b", "Хаскел", out, flags=re.I)
    out = re.sub(r"\bWexler\b", "Уекслер", out, flags=re.I)
    out = re.sub(r"\bХаскела\s+Уекслера\b", "Хаскел Уекслер", out, flags=re.I)
    out = re.sub(r"\bяк\s+Хаскела\s+Уекслера\b", "як Хаскел Уекслер", out, flags=re.I)
    out = re.sub(r"\bрозповів\s+Хаскелу\b", "розповів Хаскелу", out, flags=re.I)
    return out


def normalize_usc_case_uk(text: str) -> str:
    """Collapse leftover full university names; keep USC."""
    out = str(text or "")
    out = re.sub(
        r"\bдо\s+Університет(?:у|ом|і)?\s+Південної\s+Каліфорнії\b",
        "до USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bз\s+Університет(?:у|ом|і)?\s+Південної\s+Каліфорнії\b",
        "з USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\b(?:в|у)\s+Університет(?:у|ом|і)?\s+Південної\s+Каліфорнії\b",
        "в USC",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bУніверситет(?:у|ом|і)?\s+Південної\s+Каліфорнії\b",
        "USC",
        out,
        flags=re.I,
    )
    return out


def apply_uk_dub_name_polish(text: str, *, original: str = "") -> str:
    out = collapse_uk_title_quotes(text)
    out = normalize_george_jr_uk(out)
    # «на ім'я» always takes nominative — canon Jr. genitive must not win here
    out = re.sub(
        r"\bна\s+ім['']я\s+Джорджа-молодшого\b",
        f"на ім'я {GEORGE_JR_UK_NOM}",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bна\s+ім['']я\s+Джорджа\b",
        "на ім'я Джордж",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\b(?:в|у)\s+житті\s+Джордж-молодший\b",
        "у житті Джорджа-молодшого",
        out,
        flags=re.I,
    )
    out = re.sub(r"\bжиття\s+Георга\b", "життя Джорджа", out, flags=re.I)
    # «момент у житті Джорджа-молодшого … життя Джорджа» → один референт
    out = re.sub(
        r"\b(момент\s+у\s+житті\s+Джорджа-молодшого[^.]*?)\s+життя\s+Джорджа\b",
        r"\1 його життя",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bзмінив\s+життя\s+Джорджа\s+назавжди\b",
        "змінив його життя назавжди",
        out,
        flags=re.I,
    )
    out = normalize_usc_uk(out, original=original)
    out = normalize_usc_uk_phrase_fixes(out)
    out = normalize_usc_case_uk(out)
    out = normalize_haskell_wexler_uk(out, original=original)
    out = normalize_star_wars_uk(out)
    out = re.sub(r"\bGeorge\s+Lucas\b", GEORGE_LUCAS_UK, out, flags=re.I)
    # Split glued «Джордж-молодший Сьогодні» into two sentences.
    out = re.sub(
        r"(Джордж-молодший)\s+[Сс]ьогодні\b",
        r"\1. Сьогодні",
        out,
    )
    return out.strip()
