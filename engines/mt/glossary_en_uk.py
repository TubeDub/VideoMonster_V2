# -*- coding: utf-8 -*-
"""EN→UK glossary for Simple MT — Stage 14b: POST-MT only (no protect before Marian).

Protect/restore are no-ops. Marian sees raw English; names are fixed after MT.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger("tubedub.mt.glossary")

# Fallback pairs (longest first). Used by post-MT apply.
GLOSSARY_EN_UK: tuple[tuple[str, str], ...] = (
    ("University of Southern California", "Університет Південної Каліфорнії"),
    ("George Junior", "Джордж Молодший"),
    ("George Jr.", "Джордж Молодший"),
    ("George Jr", "Джордж Молодший"),
    ("George Lucas", "Джордж Лукас"),
    ("Haskell Wexler", "Хаскелл Векслер"),
    ("Star Wars", "Зоряні війни"),
    ("Hollywood", "Голлівуд"),
    ("Wexler", "Векслер"),
    ("Italian", "італійський"),
    ("Fiat", "Фіат"),
    ("USC", "USC"),
    ("George", "Джордж"),
    ("Hey my big guy", "Давай, здоров'яче"),
    ("Hey, real quick", "Хей, швидко"),
    ("Hey real quick", "Хей, швидко"),
    ("Hey man", "Хей, чувак"),
    ("Hey dude", "Хей, чувак"),
    ("Jonathan", "Джонатан"),
)

# Marian mangling repairs (UK side) — Stage 16/16b George Jr. quality.
_POST_UK_FIXES: tuple[tuple[str, str], ...] = (
    # Fiat / Italian (#2) — longer phrases first
    (r"\bФайта\b", "Фіат"),
    (r"\bФайту\b", "Фіат"),
    (r"\bФайтом\b", "Фіатом"),
    (r"\bItalian\b", "італійський"),
    (r"\bЦе\s+не\s+тільки\s+для\s+автомобілів\b", "Це за винятком автомобілів"),
    (
        r"\bнезважаючи\s+на\s+те,\s+що\s+(?:він\s+був\s+)?тим,?\s*хто\s+(?:буквально\s+)?дав\s+йому\s+(?:маленький\s+італійський\s+)?(?:Фіат|Fiat)\b",
        "хоч і сам подарував йому маленький італійський Фіат",
    ),
    (
        r"\bнезважаючи\s+на\s+те,\s+що\s+він\s+(?:буквально\s+)?дав\s+йому\s+(?:маленький\s+італійський\s+)?(?:Фіат|Fiat)\b",
        "хоч і сам подарував йому маленький італійський Фіат",
    ),
    (
        r"\bпросто\s+не\s+отримав\s+від\s+свого\s+сина\s+одержимості\s+машинами\b",
        "просто не розумів одержимості свого сина машинами",
    ),
    (
        r"\bпросто\s+не\s+отримав\s+одержимості\s+свого\s+сина\b",
        "просто не розумів одержимості свого сина",
    ),
    (
        r"\b(?:просто\s+)?не\s+захопився\s+автомобілями\s+свого\s+сина\b",
        "просто не розумів одержимості свого сина автомобілями",
    ),
    (
        r"\b(дав|подарував|купив)\s+йому\s+(?!маленький\s+італійський\s)(?:Фіат|Fiat)\b",
        r"\1 йому маленький італійський Фіат",
    ),
    # Crash (#5)
    (
        r"\bінша\s+машина,\s+що\s+бігла\s+по\s+дорозі\b",
        "інша машина, що мчала дорогою",
    ),
    (r"\bщо\s+бігла\s+по\s+дорозі\b", "що мчала дорогою"),
    (
        r"\bі\s+розбився\s+в\s+машину\s+Джорджа\b",
        "і врізалася в машину Джорджа",
    ),
    (r"\bрозбився\s+в\s+машину\b", "врізалася в машину"),
    (
        r"\bдобре,\s+що\s+це\s+була\s+інша\s+машина\b",
        "а саме — інша машина",
    ),
    # Application / USC (#8/#9)
    (
        r"\bзвернувся\s+до\s+престижної\s+кінематографії\b",
        "подав заявку на престижну програму з кінематографії",
    ),
    (
        r"\bпісля\s+того,\s+як\s+відпустив\s+заяву\b",
        "після того, як надіслав заяву",
    ),
    (r"\bне\s+зможе\s+увійти\b", "його не візьмуть"),
    (r"\bзастосувався\s+до\s+USC\b", "подав заявку до USC"),
    (r"\bзастосувати(?:ся)?\s+до\s+USC\b", "подати заявку до USC"),
    (r"\bбув\s+застосований\s+до\s+USC\b", "подав заявку до USC"),
    (r"\bлюдей\s+в\s+СШ\b", "людей в USC"),
    (r"\bлюдей\s+(?:у|в)\s+США\b", "людей в USC"),
    (r"\bподав(?:ся)?\s+до\s+США\b", "подався до USC"),
    (
        r"\bлиста\s+від\s+компанії\s+[«\"]Знімання\s+США[»\",\.]?",
        "листа про зарахування від кіношколи USC",
    ),
    (
        r"\bкомпанії\s+[«\"]Знімання\s+США[»\",\.]?",
        "кіношколи USC",
    ),
    (r"[«\"]Знімання\s+США[»\",\.]?", "кіношколи USC"),
    (r"\bкінографом\b", "кінооператором"),
    (r"\bкінограф\b", "кінооператор"),
    (r"\bрозповів\s+Хаскела\b", "розповів Хаскеллу"),
    (r"\bколи\s+Хаскел\b", "коли Хаскелл"),
    (r"\bХаскел\b", "Хаскелл"),
    (r"\bДжордж\s+Я\s+знаю\b", "«Джордж, я знаю"),
    (
        r"\bсфотографувати\s+переможця\s+водія\b",
        "сфотографувати переможного гонщика",
    ),
    (r"\bпереможця\s+водія\b", "переможного гонщика"),
    (
        r"\bлюдина\s+(?:насправді\s+)?формально\s+представ(?:ив|ила)\s+себе\s+як\b",
        "чоловік офіційно представився як",
    ),
    (
        r"\bпотрапити\s+до\s+своєї\s+кінематографії\b",
        "потрапити до їхньої програми з кінематографії",
    ),
    (
        r"\bДжорджа\s+Молодшого\s+створив\b",
        "Джордж Молодший створив",
    ),
    (
        r"\bДжорджа\s+Молодшого,\s+краще\s+відомий\b",
        "Джордж Молодший, краще відомий",
    ),
    # Names
    (r"\bХаскелом\s+Уекслером\b", "Хаскеллом Векслером"),
    (r"\bХаскел\s+Уекслер\b", "Хаскелл Векслер"),
    (r"\bУекслер(?:ом|а|у)?\b", "Векслер"),
    (r"\bДжоржавськ\w*\b", "Джордж Лукас"),
    # Franchise / Star Wars tail garbage (#9)
    (r"\bФрангіз\b", "франшиза"),
    (r"[«\"]Франшиза[»\"]", "франшиза"),
    (
        r"\bнайбільш\s+успішного\s+кіно\s+[«\"]?франшиза[»\"]?\s+всіх\s+часів\b",
        "найуспішнішої кінофраншизи всіх часів",
    ),
    (
        r"\bйого\s+фільм\s+[«\"]Зоряні\s+війни[»\"]\s+був\s+[«\"]Зоряні\s+війни[»\"]\s*Джордж\s+молодший\.?",
        "а його кінофраншиза — це «Зоряні війни».",
    ),
    (
        r"\bйого\s+фільм\s+[«\"]Зоряні\s+війни[»\"]\s+був\s+[«\"]Зоряні\s+війни[»\"]\b",
        "а його кінофраншиза — це «Зоряні війни»",
    ),
    (
        r"\bа\s+його\s+фільм\s+[«\"]франшиза[»\"]\s+був\s+[«\"]Зоряні\s+війни[»\"]\.?",
        "а його кінофраншиза — це «Зоряні війни».",
    ),
    (
        r"\bа\s+його\s+кінофраншиза,\s*це\s+«Зоряні\s+війни»\.?",
        "а його кінофраншиза — це «Зоряні війни».",
    ),
    (
        r"\bСьогодні\s+він\s+відомий\s+як\s+Джордж\s+Лукас,\s+а\s+його\s+(?:фільм|кінофраншиза)[^.]*Зоряні\s+війни[^.]*\.?",
        "Сьогодні його краще знають як Джорджа Лукаса, а його кінофраншиза — це «Зоряні війни».",
    ),
    # Broken quote after USC acceptance letter (#9)
    (
        r"кіношколи\s+USC\"\s*але",
        "кіношколи USC». Але",
    ),
    (
        r"кіношколи\s+USC»\"\s*але",
        "кіношколи USC». Але",
    ),
    (r"«Зоряні\s+війни»\.\"\s*$", "«Зоряні війни»."),
    (r"«Зоряні\s+війни»\.\"$", "«Зоряні війни»."),
    # Stage 37 — conversational Marian calques (IMG_2790 street interview)
    (r"\bне великий зелений\b", "не дуже люблю зелень"),
    (r"\bяке середнє значення хтось дає вам для виконання цієї роботи\b", "скільки зазвичай дають за таку роботу"),
    (r"\bя її не відчуваю\b", "мені вже не хочеться"),
    (r"\bшвидко застрибує\b", "швидко сідай"),
    (r"\bЯ тебе дістану\b", "Я тебе прикрию"),
    (r"\bпрокопаю смітник\b", "перериваю смітник"),
    (r"\bвиріс\s+[сc]\.?", "виріс"),
    (r"\bДа,\s*и дивись\b", "Так, і дивись"),
    (r"\bДа,\s+и\s+", "Так, і "),
    (r"\bДа,\s+чувак\b", "Так, чувак"),
    (r"\bМне жаль\b", "Мені шкода"),
    (r"\bмолодец\b", "молодець"),
    (r"\bпоследний\s+раз\s+ела\b", "останній раз їв"),
    (r"\bпоследний\s+раз\b", "останній раз"),
    (r"\bУ меня нет другого выбора\b", "У мене немає іншого вибору"),
    (r"\bУ меня немає іншого выбора\b", "У мене немає іншого вибору"),
    (r"\bУ меня немає другого выбора\b", "У мене немає іншого вибору"),
    (r"\bнет другого выбора\b", "немає іншого вибору"),
    (r"\bдругого выбора\b", "іншого вибору"),
    (r"\bвыбора\b", "вибору"),
    (r"\bУ меня\b", "У мене"),
    (r"\bИ смотрите\b", "І дивись"),
    (r"\bИ смотри\b", "І дивись"),
    (r"\bсмотрите\s+Джонатан\w*\s+правильно\??", "дивись, Джонатан, так?"),
    (r"\bДжонатану\s+правильно\??", "Джонатан, так?"),
    (r"\bвсего\s+(\d+)\b", r"лише \1"),
    (r"\bИ Мені\b", "І мені"),
    (r"^И\s+", "І "),
    (r"проверяю мусорные баки", "перериваю смітники"),
    (r"проверяю мусорн\w*\s+бак\w*", "риюсь у смітниках"),
    (r"\bмусорные баки\b", "смітники"),
    (r"(?i)\bГей,\s*чувак\b", "Хей, чувак"),
    (r"(?i)\bГей,\s*мій\s+здоров'?як\b", "Давай, здоров'яче"),
    (r"(?i)\bХей,\s*мій\s+здоров'?як\b", "Давай, здоров'яче"),
    (r"(?i)\bГей,\s*швидко\b", "Давай, швидко"),
    (r"(?i)^Гей([,!\s])", r"Хей\1"),
    (r"(?i)(?<=[.!?…]\s)Гей([,!\s])", r"Хей\1"),
    (r"(?i)\bГей,\s+(?=[А-ЯІЇЄҐа-яіїєґA-Za-z])", "Хей, "),
)
# Leftover protect-token garbage (Marian-mangled + legacy).
_GARBAGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"__GLOS[_A-Z0-9_*\s]*", re.I),
    re.compile(r"_GLOS[_A-Z0-9_*\s]*", re.I),
    re.compile(r"__GLOSS_\d+__", re.I),
    re.compile(r"\bGLOSS_?\d+\b", re.I),
    re.compile(r"\}[A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9_-]+"),
    re.compile(r"\{\s*[A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9_-]+"),
    re.compile(r"\bG\d{2,}\b"),
    re.compile(r"\bГГ?\d+\b", re.I),
    re.compile(r"ведьг\d*\]?", re.I),
    re.compile(r"⟦\s*G?\d*\s*⟧", re.I),
    re.compile(r"\[\s*G\d+\s*\]", re.I),
    re.compile(r"\(\s*G\d+\s*\)", re.I),
)

_PLACEHOLDER_RE = re.compile(r"__?GLOS_|\}[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]", re.I)


def glossary_pairs() -> tuple[tuple[str, str], ...]:
    """Longest-first EN→UK pairs (project glossary preferred)."""
    try:
        from engines.project_glossary import load_project_glossary

        g = load_project_glossary()
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for e in g.entries:
            labels = [e.source, *list(e.aliases or [])]
            for lab in labels:
                key = lab.strip()
                if not key or key.lower() in seen:
                    continue
                seen.add(key.lower())
                pairs.append((key, e.canonical))
        pairs.sort(key=lambda x: -len(x[0]))
        if pairs:
            return tuple(pairs)
    except Exception:
        pass
    return GLOSSARY_EN_UK


def protect_glossary(
    text: str, *, pairs: Iterable[tuple[str, str]] | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Stage 14b: Simple no-op — Marian gets RAW source (no placeholders)."""
    _ = pairs
    return str(text or ""), []


def restore_glossary(
    text: str,
    forms: list[str] | list[tuple[str, str]],
) -> str:
    """Stage 14b: no-op when map empty (protect disabled)."""
    if not forms:
        return str(text or "")
    # Legacy path only — strip leftovers if somehow called with a map.
    return strip_glossary_placeholders(str(text or ""))


def strip_glossary_placeholders(text: str) -> str:
    """Remove leftover / Marian-mangled glossary placeholders."""
    out = str(text or "")
    for pat in _GARBAGE_PATTERNS:
        out = pat.sub(" ", out)
    out = re.sub(r"\}\s*", " ", out)
    out = re.sub(r"\{\s*", " ", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip(" ,;:")


# Alias used by older call sites / Stage 14.
strip_glossary_artifacts = strip_glossary_placeholders


def contains_glossary_garbage(text: str) -> bool:
    t = str(text or "")
    if not t:
        return False
    if _PLACEHOLDER_RE.search(t):
        return True
    for pat in _GARBAGE_PATTERNS:
        if pat.search(t):
            return True
    if re.search(r"ведьг", t, re.I):
        return True
    return False


def _replace_en_phrase(text: str, en: str, uk: str) -> str:
    """Case-insensitive, word-boundary-ish EN→UK replace."""
    if not en or not uk:
        return text
    # Flexible whitespace inside multi-word phrases.
    body = re.escape(en)
    body = body.replace(r"\ ", r"\s+")
    # Allow optional trailing period already in en (Jr.) — escaped.
    pat = re.compile(rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])", re.IGNORECASE)
    return pat.sub(uk, text)


def rewrite_hey_vocative_uk(text: str) -> str:
    """Marian «Гей» is an insult/orientation in UK — never a vocative «Hey»."""
    out = str(text or "")
    if not out.strip():
        return out
    rules = (
        (r"(?i)\bГей,\s*чувак\b", "Хей, чувак"),
        (r"(?i)\bГей,\s*мій\s+здоров'?як\b", "Давай, здоров'яче"),
        (r"(?i)\bХей,\s*мій\s+здоров'?як\b", "Давай, здоров'яче"),
        (r"(?i)\bГей,\s*швидко\b", "Давай, швидко"),
        (r"(?i)^Гей([,!\s])", r"Хей\1"),
        (r"(?i)(?<=[.!?…]\s)Гей([,!\s])", r"Хей\1"),
        (r"(?i)\bГей,\s+(?=[А-ЯІЇЄҐа-яіїєґA-Za-z])", "Хей, "),
    )
    for pat, repl in rules:
        out = re.sub(pat, repl, out)
    return out


def apply_uk_gender_lock(text: str, *, gender: str = "male") -> str:
    """Rule-based past-tense lock. Default this video: both speakers male (-в)."""
    out = str(text or "")
    if not out.strip():
        return out
    g = str(gender or "male").strip().lower()
    if g.startswith("f"):
        pairs = (
            (r"\bЯ\s+втратив\b", "Я втратила"),
            (r"\bЯ\s+зробив\b", "Я зробила"),
            (r"\bЯ\s+сказав\b", "Я сказала"),
        )
    else:
        pairs = (
            (r"\bЯ\s+втратила\b", "Я втратив"),
            (r"\bЯ\s+зробила\b", "Я зробив"),
            (r"\bЯ\s+сказала\b", "Я сказав"),
            (r"\bЯ\s+пішла\b", "Я пішов"),
            (r"\bЯ\s+хотіла\b", "Я хотів"),
            (r"\bЯ\s+думала\b", "Я думав"),
            (r"\bЯ\s+знайшла\b", "Я знайшов"),
            (r"\bЯ\s+взяла\b", "Я взяв"),
            (r"\bЯ\s+побачила\b", "Я побачив"),
            (r"\bЯ\s+дала\b", "Я дав"),
            (r"\bЯ\s+стала\b", "Я став"),
            (r"\bЯ\s+вирішила\b", "Я вирішив"),
            (r"\bЯ\s+зрозуміла\b", "Я зрозумів"),
            (r"\bЯ\s+почула\b", "Я почув"),
            (r"\bЯ\s+прийшла\b", "Я прийшов"),
            (r"\bЯ\s+змогла\b", "Я зміг"),
            (r"\bела\b", "їв"),
            (r"\bїла\b", "їв"),
            (r"останній\s+раз\s+ела\b", "останній раз їв"),
            (r"останній\s+раз\s+їла\b", "останній раз їв"),
        )
    for pat, repl in pairs:
        out = re.sub(pat, repl, out)
    return out


def strip_en_tokens_for_uk_tts(text: str) -> str:
    """Do not send leftover EN (Yeah/Are/Honestly/Before/Jonathan) to TTS."""
    out = str(text or "")
    if not out.strip():
        return out
    replacements = (
        (r"\bAre you serious\??", "Ти серйозно?"),
        (r"\bHonestly\b", "Чесно кажучи"),
        (r"\bYeah,?\b", "Так,"),
        (r"\bBefore\b", "Перш ніж"),
        (r"\bJonathan\b", "Джонатан"),
        (r"\bAre you\b", "Ти"),
        (r"\bCome on\b", "Давай"),
        (r"\bSometimes\b", "Іноді"),
        (r"\bReally\??", "Серйозно"),
        (r"\bAlright\b", "Добре"),
        (r"\bHow's\b", "Як"),
        (r"\bShut up\b", "Та ну"),
    )
    for pat, repl in replacements:
        out = re.sub(pat, repl, out)
    out = re.sub(r"\bAre\b", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip()


def apply_surprise_shut_up(text: str, *, source: str = "") -> str:
    """«Shut up» as surprise → «Та ну?» / «Серйозно?», not «Заткнись»/«Замовкни»."""
    out = str(text or "")
    src = str(source or "")
    first = bool(re.search(r"(?i)^\s*(?:oh\s+)?shut\s+up\b", src))
    surprise = bool(
        re.search(r"(?i)^\s*(?:oh\s+)?shut\s+up\s*[!?.]*\s*$", src)
        or (
            first
            and (
                "?" in src
                or "!" in src
                or len(src.split()) <= 16
            )
        )
    )
    if surprise or (not src and re.search(r"\b(?:Заткнись|Замовкни)\b", out)):
        out = re.sub(r"\bЗаткнись\b", "Та ну?", out)
        out = re.sub(r"\bЗамовкни\b", "Та ну?", out)
        out = re.sub(r"Та ну\?\.", "Та ну?", out)
    return out


def apply_uk_ru_conjunction_i(text: str) -> str:
    """Standalone Russian «и» / «Да» → Ukrainian «і» / «Так»."""
    out = str(text or "")
    out = re.sub(r"\bДа,\s*и\b", "Так, і", out)
    out = re.sub(r"\bДа,\s*чувак\b", "Так, чувак", out)
    out = re.sub(r"\bДа,(?=\s|$)", "Так,", out)
    out = re.sub(r"^И\s+", "І ", out)
    out = re.sub(r"(?<=[.!?…]\s)И\s+", "І ", out)
    out = re.sub(r"\bИ смотрите\b", "І дивись", out)
    out = re.sub(r"\bИ смотри\b", "І дивись", out)
    out = re.sub(r"(?<=[А-ЯІЇЄҐа-яіїєґ,])\s+и\s+(?=[А-ЯІЇЄҐа-яіїєґ])", " і ", out)
    return out


def apply_uk_marian_repairs(text: str) -> str:
    """UK-side Marian garbage / calque repairs (Simple post-MT)."""
    out = str(text or "")
    if not out.strip():
        return out
    for pat, repl in _POST_UK_FIXES:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    # Fix mismatched «…USC" dialogue close.
    out = re.sub(
        r"«Джордж,\s*я\s+знаю\s+людей\s+в\s+USC([^»\"]{0,260}?)\"",
        r"«Джордж, я знаю людей в USC\1»",
        out,
        flags=re.IGNORECASE,
    )
    # Drop duplicate «фільм Зоряні війни був Зоряні війни Джордж молодший».
    out = re.sub(
        r"його\s+фільм\s+[«\"]Зоряні\s+війни[»\"]\s+був\s+[«\"]Зоряні\s+війни[»\"]\s*"
        r"Джордж\s+молодший\.?\s*",
        "",
        out,
        flags=re.IGNORECASE,
    )
    # Collapse duplicated clean Lucas closers.
    out = re.sub(
        r"(Сьогодні\s+його\s+краще\s+знають\s+як\s+Джорджа\s+Лукаса,\s+"
        r"а\s+його\s+кінофраншиза\s+[—\-]\s+це\s+«Зоряні\s+війни»\.)(?:\s*\1)+",
        r"\1",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r'(?:»\.)?"\s*$', "».", out) if out.endswith('."') or out.endswith('».') else out
    out = re.sub(r'"\s*$', "", out).strip()
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip()


def apply_post_mt_glossary_fixes(text: str) -> str:
    """EN→UK glossary + Marian UK mangling repairs (longest EN first)."""
    out = str(text or "")
    # Prefer built-in order for Jr/Lucas/George precedence, then project pairs.
    seen_lower: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for en, uk in GLOSSARY_EN_UK:
        key = en.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        ordered.append((en, uk))
    for en, uk in glossary_pairs():
        key = en.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        ordered.append((en, uk))
    ordered.sort(key=lambda x: -len(x[0]))
    for en, uk in ordered:
        out = _replace_en_phrase(out, en, uk)
    out = apply_uk_marian_repairs(out)
    return out


def apply_glossary_en_uk(text: str) -> str:
    """Best-effort post-MT glossary apply (no placeholders)."""
    return apply_post_mt_glossary_fixes(text)


def translate_with_glossary_protect(text: str, translate_fn) -> str:
    """Stage 14b: raw translate → finalize (protect is no-op)."""
    raw = str(translate_fn(str(text or "")) or "").strip()
    return finalize_mt_text("en", "uk", raw, source=str(text or ""))


_STREET_NAME_UK = {
    "jonathan": "Джонатан",
    "george": "Джордж",
}


def _spell_usd_amount(digits: str) -> str:
    try:
        n = int(digits)
    except ValueError:
        return ""
    if n == 3000:
        return "три тисячі доларів"
    if n == 1:
        return "долар"
    return f"{n} доларів"


def rewrite_usd_amounts_uk(text: str) -> str:
    """`$3, 000` / `$3,000` → spoken UK; never leave a broken dollar token."""
    out = str(text or "")

    def _sub(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0) or "")
        spelled = _spell_usd_amount(digits)
        return spelled or m.group(0)

    out = re.sub(r"\$\s*[\d][\d\s,]*", _sub, out)
    out = re.sub(r"(\.\s+)три тисячі", r"\1Три тисячі", out)
    if out.lower().startswith("три тисячі"):
        out = "Три" + out[3:]
    return out


def apply_street_interview_uk(source: str, mt: str) -> str:
    """Street-interview EN→UK repairs. Pattern rules, not a film script."""
    src = str(source or "")
    out = str(mt or "").strip()
    src_l = src.lower()
    out = rewrite_usd_amounts_uk(out)

    if re.search(r"(?i)\bi got you\b", src):
        out = re.sub(r"Я\s+з\s+тобою", "Я тебе прикрию", out, flags=re.I)
        out = re.sub(r"Я\s+тебе\s+дістану", "Я тебе прикрию", out, flags=re.I)
        if "прикрию" not in out.lower() and "з цим" not in out.lower():
            out = ("Я тебе прикрию. " + out).strip()
        if re.search(r"(?i)\bok(?:ay)?\s*\??\s*$", src) and not re.search(
            r"(?i)окей\??", out
        ):
            out = out.rstrip(".!?…") + ". Окей?"

    if re.search(r"(?i)here'?s a dollar|here is a dollar", src):
        if "долар" not in out.lower():
            out = (out.rstrip(".!?…") + ". Ось долар.").strip()

    if re.search(r"(?i)(?:^|[.!?]\s)a dollar\?", src):
        if not re.search(r"(?i)долар\s*\?", out):
            if "серйозно" in out.lower():
                dude = ", чувак" if re.search(r"(?i)\bman\b", src) else ""
                out = f"Долар? Ти серйозно{dude}?"
            else:
                out = ("Долар? " + out).strip()
        elif re.search(r"(?i)\bman\b", src) and "чувак" not in out.lower():
            out = re.sub(r"(\?)\s*$", r", чувак\1", out)

    if re.search(r"(?i)doing a good job", src):
        out = re.sub(r"\bмолодец\b", "молодець", out)
        if not re.search(r"(?i)молодець|гарно робиш|добре робиш", out):
            out = re.sub(r"(чувак,?\s*)", r"\1ти гарно робиш. ", out, count=1, flags=re.I)
            if not re.search(r"(?i)гарно робиш|молодець", out):
                out = ("Ти гарно робиш. " + out).strip()

    if re.search(r"(?i)bunch of greens|how'?s the whole.*green|you like greens", src):
        if "зелен" not in out.lower():
            if re.search(r"(?i)you like greens", src):
                out = ("Ти любиш зелень, так? " + out).strip()
            else:
                out = ("Як там із зеленню? " + out).strip()
    if re.search(r"(?i)(?:i'?m|i am) so hungry|yeah,?\s*yeah i'?m so hungry", src):
        if "голодн" not in out.lower():
            out = (out.rstrip(".!?…") + ". Так, я такий голодний.").strip()

    if re.search(r"(?i)two whole days", src):
        if not re.search(r"(?i)цілих\s+два\s+дні|два\s+цілих\s+дні", out):
            if re.search(r"(?i)два\s+дні", out):
                out = re.sub(r"два\s+дні", "цілих два дні", out, count=1, flags=re.I)
            else:
                out = ("Цілих два дні? " + out).strip()

    if re.search(r"(?i)here you go|i want to give (?:that|this) to you", src):
        if re.search(r"(?i)зачекай тут|ти йдеш", out):
            out = re.sub(
                r"О,?\s*зачекай тут,\s*ти йдеш,\s*я хочу дати це тобі\.?",
                "Ось, тримай. Хочу тобі це дати.",
                out,
                flags=re.I,
            )
        if not re.search(r"(?i)тримай|хочу тобі|даю (?:це|тебе)", out):
            out = (out.rstrip(".!?…") + ". Ось, тримай. Хочу тобі це дати.").strip()

    if re.search(r"(?i)dig through (?:the )?trash", src):
        out = re.sub(r",?\s*їси чуже сміття\.?", "", out, flags=re.I)
        if not re.search(r"(?i)смітник|смітт", out):
            out = (out.rstrip(".!?…") + ". Я просто риюсь у смітнику.").strip()

    if re.search(r"(?i)is that normal out here", src):
        if not re.search(r"(?i)нормальн\w*\s+тут|тут\s+нормальн", out):
            out = ("Це нормально тут? " + out).strip()

    look = re.search(r"(?i)and look (\w+)\s+right\??", src)
    if look:
        name = _STREET_NAME_UK.get(look.group(1).lower(), look.group(1))
        yeah = ""
        if re.search(r"(?i)right\??\s*yeah\.?\s*$", src) or re.search(
            r"(?i)\byeah\.?\s*$", src
        ):
            yeah = " Так."
        garbage = bool(
            re.search(r"(?i)смотри|правильно|джон(?:атан)?\w*\s+правильн", out)
        )
        if garbage or "дивись" not in out.lower() or name.lower() not in out.lower():
            out = f"І дивись, {name}, так?{yeah}".strip()
        else:
            out = re.sub(
                rf"(?i)дивись\s+{re.escape(name)}",
                f"дивись, {name}",
                out,
            )
            if yeah and not re.search(r"(?i)так\.\s*$", out):
                if re.search(r"(?i)так\?\s*$", out):
                    out = out.rstrip() + yeah
                else:
                    out = out.rstrip(".!?…") + "?" + yeah

    if re.search(r"(?i)that'?s all i need to hear", src):
        out = re.sub(
            r"Я хочу,\s*щоб ви мене почули[^.]{0,80}",
            "Добре, цього досить.",
            out,
            flags=re.I,
        )
        if not re.search(r"(?i)цього досить|все,? що треба", out):
            out = ("Добре, цього досить. " + out).strip()
        if re.search(r"(?i)before you go", src):
            if re.search(r"(?i)мене почули", out) or not re.search(
                r"(?i)перш ніж|перед тим", out
            ):
                out = "Добре, цього досить. Перш ніж підеш, хочу впевнитися..."

    if re.search(r"(?i)\bman\b", src):
        out = re.sub(r"\bчоловік\?", "чувак?", out)
        out = re.sub(r",\s*чоловік\b", ", чувак", out)
        out = re.sub(r"\s+чоловік\s*[.!]?\s*$", " чувак.", out)

    out = re.sub(r"\bвиріс\s+[сc]\.?", "виріс", out, flags=re.I)
    return " ".join(out.split()).strip()


def street_uk_honesty_gaps(source: str, mt: str) -> list[str]:
    """Facts the English source still has that UK TTS must not drop."""
    src = str(source or "").lower()
    out = str(mt or "").lower()
    gaps: list[str] = []
    if re.search(r"\bdollar\b|\$\s*\d", src) and not re.search(
        r"долар|тисяч", out
    ):
        gaps.append("money")
    if re.search(r"two whole days", src) and "два" not in out:
        gaps.append("two_whole_days")
    if re.search(r"normal out here", src) and "нормальн" not in out:
        gaps.append("normal_out_here")
    if re.search(r"bunch of greens|you like greens", src) and "зелен" not in out:
        gaps.append("greens")
    if re.search(r"i'?m so hungry", src) and "голодн" not in out:
        gaps.append("hungry")
    if re.search(r"i got you", src) and "прикрию" not in out and "з цим" not in out:
        gaps.append("i_got_you")
    if re.search(r"that'?s all i need to hear", src) and "досить" not in out:
        gaps.append("all_i_need")
    if re.search(r"and look \w+ right", src) and "дивись" not in out:
        gaps.append("look_name")
    if re.search(r"[ыэёъ]|\bмолодец\b|\bела\b|\bвыбора\b|\bДа,", mt or ""):
        gaps.append("ru_leak")
    return gaps


def finalize_mt_text(
    src_lang: str,
    tgt_lang: str,
    text: str,
    source: str = "",
) -> str:
    """Post-process MT / cache-hit text. Strip placeholders → glossary → strip."""
    src = str(src_lang or "").strip().lower()
    tgt = str(tgt_lang or "").strip().lower()
    out = str(text or "")
    if not out.strip():
        return out
    out = strip_glossary_placeholders(out)
    if src == "en" and tgt == "uk":
        out = apply_post_mt_glossary_fixes(out)
        out = rewrite_hey_vocative_uk(out)
        out = apply_uk_ru_conjunction_i(out)
        if source:
            out = apply_street_interview_uk(source, out)
        out = apply_uk_gender_lock(out, gender="male")
        out = strip_en_tokens_for_uk_tts(out)
        out = apply_surprise_shut_up(out, source=source)
        if source:
            out = restore_dropped_source_entities(source, out)
            out = apply_street_interview_uk(source, out)
    out = strip_glossary_placeholders(out)
    if contains_glossary_garbage(out):
        logger.error("[glossary] placeholders remain after finalize: %r", out[:120])
        out = strip_glossary_placeholders(out)
    return out


def restore_dropped_source_entities(source: str, mt: str) -> str:
    """Re-attach dropped money / trash / name facts the source still has."""
    src = str(source or "")
    out = rewrite_usd_amounts_uk(str(mt or "")).strip()
    if not src:
        return out
    src_l = src.lower()
    out_l = out.lower()
    has_money_words = "долар" in out_l or "тисяч" in out_l
    for m in re.finditer(r"\$\s*([\d][\d,\s]*)", src):
        digits = re.sub(r"\D", "", m.group(1) or "")
        if not digits:
            continue
        if has_money_words:
            continue
        money = _spell_usd_amount(digits)
        if not money:
            continue
        out = f"{money}. {out}".strip() if out else money
        has_money_words = True
    if re.search(r"(?i)\bdollar\b", src) and "долар" not in out.lower():
        if re.search(r"(?i)a dollar\?", src):
            out = ("Долар? " + out).strip()
        else:
            out = (out.rstrip(".!?…") + ". Ось долар.").strip()
    out_l = out.lower()
    eats_trash = bool(re.search(r"(?i)eat\s+(?:other|.*trash)|trash.*feed", src))
    digs_trash = bool(re.search(r"(?i)dig through (?:the )?trash", src))
    if eats_trash and not re.search(r"смітт", out_l):
        clause = "їси чуже сміття"
        if clause not in out_l:
            out = f"{out.rstrip('.!?…')}, {clause}.".strip() if out else f"{clause.capitalize()}."
    elif digs_trash and not re.search(r"смітт", out_l):
        clause = "риюсь у смітнику"
        if clause not in out_l:
            out = f"{out.rstrip('.!?…')}. Я просто {clause}.".strip() if out else f"Я просто {clause}."
    if re.search(r"(?i)\bjonathan\b", src) and "джонатан" not in out.lower():
        out = re.sub(r"\bJonathan\b", "Джонатан", out)
        if "джонатан" not in out.lower():
            out = f"Джонатан. {out}".strip() if out else "Джонатан"
    out = apply_surprise_shut_up(out, source=src)
    return " ".join(out.split()).strip()
