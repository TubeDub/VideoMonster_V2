"""UK literary dub polish — freer phrasing than calque post-edit.

Goal: keep meaning / entities / facts, but allow native word order and idiom.
Works offline (regex) when LLM is unavailable; LLM literary pass uses the same cues.
"""

from __future__ import annotations

import os
import re
from typing import Any


# Stiffness / English-structure cues that mean "rules-only is not enough"
_STIFFNESS_PATTERNS: list[tuple[str, str]] = [
    (r"\bякий\s+називається\b", "rel_called"),
    (r"\bяка\s+називається\b", "rel_called_f"),
    (r"\bдійсно\s+майже\s+нічого\s+серйозно\b", "nothing_seriously"),
    (r"\bмайже\s+нічого\s+серйозно\s+не\s+робив\b", "nothing_seriously2"),
    (r"\bне\s+полишала?\s+тривога\b", "anxiety_calque"),
    (r"\bне\s+полишало\s+відчуття,\s+що\s+він\s+дуже\s+боявся\s+туди\s+дістатися\b", "dreading_calque"),
    (r"\bвзяти\s+деякі\s+фотографії\b", "take_some_photos"),
    (r"\bфотографії\s+переможця\b", "winner_photos_vague"),
    (r"\bпереможця\s+гонки\b", "winner_of_race_vague"),
    (r"\bвідомий\s+сьогодні,\s*як\b", "known_today_as"),
    (r"\bвідомий\s+сьогодні\s+як\b", "known_today_as2"),
    (r"\bвесь\s+світ\s+знає\b", "whole_world_knows"),
    (r"\bпросто\s+не\s+розумів\s+одержимості\s+сина\b", "obsession_calque"),
    (r"\bфактично\s+купив\b", "actually_bought"),
    (r"\bІ\s+так\s+в\s+основному\b", "basically"),
    (r"\bв\s+деякій\s+точці\b", "at_some_point"),
    (r"\bАле,\s+як\s+він\b", "but_as_he"),
    (r"\bТак\s+як\s+два\s+тижні\b", "so_as_two_weeks"),
    (r"\bдобре\s+що\s+це\s+ще\s+один\b", "well_what_it_was"),
    (r"\bнезважаючи\s+на\s+те,\s+що\s+той,\s+хто\s+буквально\b", "father_fiat_redundancy"),
    (r"\bважке\s+передчуття\b", "heavy_foreboding_ornate"),
]


# Offline literary rewrites — prefer natural dub speech over ornate “beauty”
_LITERARY_FIXES: list[tuple[str, str]] = [
    # Seg1 dinner+hometown — keep SHORT (long form exceeds ~3s slot → «на вечерю» clipped)
    (
        r"(\d+)-річний\s+хлопець\s+на\s+ім['']я\s+(Джордж-молодший)\s+проїжджав\s+"
        r"через\s+(?:своє\s+)?рідне\s+місто\s+дорогою\s+додому(?:\s+на\s+вечерю)?",
        r"\1-річний \2 їхав рідним містом додому на вечерю",
    ),
    # Dreading home — keep simple (EN: he dreaded getting home)
    (
        r"\bдорогою\s+додому\s+його\s+не\s+полишало\s+важке\s+передчуття\b",
        "йому зовсім не хотілося повертатися додому",
    ),
    (
        r"\bйого\s+не\s+полишало\s+важке\s+передчуття\b",
        "йому зовсім не хотілося їхати додому",
    ),
    (
        r"\bне\s+полишало\s+важке\s+передчуття(?:\s*[—–-]\s*зовсім\s+не\s+хотілося\s+туди\s+їхати)?\b",
        "йому зовсім не хотілося туди їхати",
    ),
    (
        r"\bДжорджа-молодшого\s+не\s+полишало\s+відчуття,\s+що\s+він\s+дуже\s+боявся\s+туди\s+дістатися\b",
        "Джорджу-молодшому зовсім не хотілося їхати додому",
    ),
    (
        r"\bДжордж-молодший\s+його\s+не\s+полишала\s+тривога,\s*як\s+він\s+був\s+дійсно\s+дуже\s+боявся\s+туди\s+дістатися\b",
        "Джорджу-молодшому зовсім не хотілося їхати додому",
    ),
    (
        r"\bДжордж-молодший\s+його\s+не\s+полишала\s+тривога,\s*як\s+він\s+був\s+дійсно\s+зі\s+страхом\s+очікував\s+насправді\s+отримати\s+там\b",
        "Джорджу-молодшому зовсім не хотілося їхати додому",
    ),
    (
        r"\bйого\s+не\s+полишала\s+тривога,\s*як\s+він\s+був\s+дійсно\s+зі\s+страхом\s+очікував\s+насправді\s+отримати\s+там\b",
        "йому зовсім не хотілося їхати додому",
    ),
    (
        r"\bзі\s+страхом\s+очікував\s+насправді\s+отримати\s+там\b",
        "зовсім не хотів їхати додому",
    ),
    (
        r"\bне\s+полишало\s+відчуття,\s+що\s+він\s+дуже\s+боявся\s+туди\s+дістатися\b",
        "йому зовсім не хотілося туди їхати",
    ),
    (
        r"\bвін\s+його\s+не\s+полишала\s+тривога,\s+що\s+йому\s+справді\s+страшно\s+туди\s+дістатися\b",
        "йому зовсім не хотілося туди їхати",
    ),
    # Nothing seriously except cars
    (
        r"\bвін\s+дійсно\s+майже\s+нічого\s+серйозно\s+не\s+робив,\s*окрім\s+автомобілів\b",
        "він майже нічим не займався по-справжньому серйозно — окрім автомобілів",
    ),
    (
        r"\bмайже\s+нічого\s+серйозно\s+не\s+робив,\s*окрім\s+автомобілів\b",
        "майже нічим не займався по-справжньому серйозно — окрім автомобілів",
    ),
    # Fiat naming — drop EN «which is called»; keep Latin brand (entity restore)
    (
        r"\bневеликий\s+італійський\s+автомобіль,\s*як(?:ий|а)\s+називається\s+(?:Фіат|Fiat)\b",
        "невеликий італійський автомобіль Fiat",
    ),
    (
        r"\bавтомобіль,\s*як(?:ий|а)\s+називається\s+(?:Фіат|Fiat)\b",
        "автомобіль Fiat",
    ),
    # Father gave Fiat but still didn't get obsession — kill EN redundancy
    (
        r"\bале\s+(?:його\s+)?батько,\s*незважаючи\s+на\s+те,\s+що\s+(?:він\s+був\s+)?т(?:им|ой),?\s*хто\s+буквально\s+дав\s+йому\s+(?:Фіат|Fiat),?\s*(?:він\s+)?",
        "але батько, хоч і сам подарував йому Fiat, ",
    ),
    (
        r"\bнезважаючи\s+на\s+те,\s+що\s+(?:він\s+був\s+)?т(?:им|ой),?\s*хто\s+буквально\s+дав\s+йому\s+(?:Фіат|Fiat)\b",
        "хоч і сам подарував йому Fiat",
    ),
    # Father obsession — keep shorter than ornate “звідки у сина”
    (
        r"\bвін\s+просто\s+не\s+розумів\s+одержимості\s+(?:свого\s+)?сина\s+автомобілями\b",
        "він ніяк не розумів, чому син так одержимий автомобілями",
    ),
    (
        r"\bпросто\s+не\s+розумів\s+одержимості\s+(?:свого\s+)?сина\s+автомобілями\b",
        "ніяк не розумів, чому син так одержимий автомобілями",
    ),
    # Photos of winning driver (EN: winning driver = гонщик, not abstract winner)
    (
        r"\bвзяти\s+деякі\s+фото(?:графії)?\s+переможного\s+гонщика\b",
        "зробити кілька фото переможного гонщика",
    ),
    (
        r"\bвзяти\s+деякі\s+фотографії\s+переможця(?:\s+гонки)?\b",
        "зробити кілька фото переможного гонщика",
    ),
    (
        r"\bвзяти\s+деякі\s+фото(?:графії)?\b",
        "зробити кілька фото",
    ),
    (
        r"\bфотографії\s+переможця(?:\s+гонки)?\b",
        "фото переможного гонщика",
    ),
    (
        r"\bфото\s+переможця\s+гонки\b",
        "фото переможного гонщика",
    ),
    (
        r"\bфотографії\s+переможного\s+заїзду\b",
        "фото переможного гонщика",
    ),
    (
        r"\bлюдина\s+фактично\s+офіційно\s+представився\b",
        "чоловік офіційно представився",
    ),
    (
        r"\bДжордж-молодший\s+викинул[ао]\b",
        "Джорджа-молодшого викинуло",
    ),
    # Known today as George Lucas — natural, not “весь світ”
    (
        r"\bСьогодні\s+Джорджа-молодшого\s+весь\s+світ\s+знає\s+як\s+Джорджа\s+Лукаса\b",
        "Сьогодні Джорджа-молодшого знають як Джорджа Лукаса",
    ),
    (
        r"\bДжордж-молодший,\s*відомий\s+сьогодні,\s*як\s+Джордж\s+Лукас\b",
        "Сьогодні Джорджа-молодшого знають як Джорджа Лукаса",
    ),
    (
        r"\bДжордж-молодший,\s*відомий\s+сьогодні\s+як\s+Джордж\s+Лукас\b",
        "Сьогодні Джорджа-молодшого знають як Джорджа Лукаса",
    ),
    (
        r"\bДжордж-молодший\s+сьогодні\s+відомий\s+як\s+Джордж\s+Лукас\b",
        "Сьогодні він відомий як Джордж Лукас",
    ),
    # Direct speech cue (father quote)
    (
        r"(?<![«\"])\bЧому\s+ти\s+не\s+можеш\b",
        "«Чому ти не можеш",
    ),
    # Discourse glue
    (r"\bІ\s+так\s+в\s+основному\b", "Тож"),
    (r"\bфактично\s+купив\b", "купив"),
    (r"\bв\s+деякій\s+точці\b", "згодом"),
    (r"\bАле,\s+як\s+він\s+їхав\b", "Але коли він їхав"),
    (r"\bАле,\s+як\s+він\s+прогулявся\b", "Поки він ішов"),
]


def literary_mode_enabled() -> bool:
    v = (os.getenv("VM_NATURALIZER_LITERARY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def detect_stiffness(text: str) -> list[str]:
    t = str(text or "")
    hits: list[str] = []
    for pat, code in _STIFFNESS_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            hits.append(code)
    return hits


def is_stiff_uk(text: str) -> bool:
    return bool(detect_stiffness(text))


def apply_literary_uk(text: str, *, original: str = "") -> tuple[str, list[str]]:
    """Apply offline literary rewrites. Returns (text, applied_codes)."""
    out = str(text or "")
    if not out.strip() or not literary_mode_enabled():
        return out, []
    applied: list[str] = []
    for pat, repl in _LITERARY_FIXES:
        new = re.sub(pat, repl, out, flags=re.IGNORECASE)
        if new != out:
            out = new
            applied.append(pat[:40])
    # Collapse double spaces / fix «Сьогодні» after period glue
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"\.\s*Сьогодні\b", ". Сьогодні", out)
    # If we rewrote identity to start with Сьогодні mid-sentence, capitalize after «і »
    out = re.sub(
        r"\bі\s+Сьогодні\s+Джорджа-молодшого",
        "і сьогодні Джорджа-молодшого",
        out,
        flags=re.I,
    )
    # Prefer sentence-initial identity rewrite
    if original and re.search(r"\bbetter known today as\b", original, re.I):
        out = re.sub(
            r"^Джордж-молодший,\s*відомий\s+сьогодні,?\s*як\s+Джордж\s+Лукас",
            "Сьогодні Джорджа-молодшого знають як Джорджа Лукаса",
            out,
            flags=re.I,
        )
    # Close opened quote for father-style questions if still open
    if "«Чому ти не можеш" in out and out.count("«") > out.count("»"):
        # Close before next sentence boundary or end
        out = re.sub(
            r"«Чому ти не можеш([^«»]+?)([.!?])(?!\s*»)",
            r"«Чому ти не можеш\1»\2",
            out,
            count=1,
        )
        if out.count("«") > out.count("»") and not out.rstrip().endswith("»"):
            out = out.rstrip().rstrip(".") + "»."
    return out.strip(), applied


def literary_prompt_extra() -> str:
    return (
        "\n\nРЕЖИМ: редактор дубляжа — естественно, просто, точно по смыслу.\n"
        "Сохрани смысл, факты, имена, бренды, даты — 100%.\n"
        "Можно полностью перестраивать фразы, но НЕ украшай сверх оригинала.\n"
        "Если EN простое — UK тоже простое (не «важке передчуття», а «не хотілося додому»).\n"
        "Убирай английский каркас и повторы (despite being the one who literally gave him…).\n"
        "Прямую речь отца оформляй в «…».\n"
        "Примеры направления:\n"
        "• dreading getting home → «йому зовсім не хотілося повертатися додому»\n"
        "• nothing seriously except cars → «майже нічим не займався по-справжньому серйозно»\n"
        "• a car called the Fiat → «невеликий італійський автомобіль Fiat»\n"
        "• photos of the winning driver → «фото переможного гонщика» (не абстрактний «переможець»)\n"
        "• better known today as George Lucas → "
        "«Сьогодні Джорджа-молодшого знають як Джорджа Лукаса»\n"
    )


def should_force_literary_llm(
    text: str,
    *,
    original: str = "",
    quality_needs_retry: bool = False,
) -> bool:
    """When True, run LLM even if rule polish already removed garbage MT."""
    if not literary_mode_enabled():
        return False
    if quality_needs_retry:
        return True
    if is_stiff_uk(text):
        return True
    # Long narrative that still smells like EN discourse glue
    t = str(text or "")
    if original and len(t.split()) >= 35:
        if re.search(
            r"\b(?:І так|Так як|Але, як|в основному|фактично|в деякій точці)\b",
            t,
            flags=re.I,
        ):
            return True
    return False


def literary_meta(text: str) -> dict[str, Any]:
    codes = detect_stiffness(text)
    return {
        "literary_stiff": bool(codes),
        "stiffness_codes": codes,
        "literary_mode": literary_mode_enabled(),
    }
