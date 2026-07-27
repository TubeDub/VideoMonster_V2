"""HF3 — Dirty MT detector: Raw==Naturalized on dirty text is a BUG."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# Temporary regex repair — TODO(HF2): move each into mask/restore or glossary
# Ticket: HOTFIX-GL-MT-NAT-TQE — temporary layer only.
_TEMP_ENTITY_REPAIRS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\bЖр\b"), "молодший", "TODO:mask_restore Jr"),
    (re.compile(r"\bДжер\b"), "Джордж", "TODO:mask_restore George"),
    (re.compile(r"\bЄр\b"), "молодший", "TODO:mask_restore Jr"),
    (re.compile(r"\bЮра\s+Джера\b", re.I), "Джордж-молодший", "TODO:mask_restore Jr"),
    (re.compile(r"\bГеоргій\s+Жр\b", re.I), "Джордж-молодший", "TODO:mask_restore Jr"),
    (re.compile(r"\bГеорга\s+Жр\.?\b", re.I), "Джорджа-молодшого", "TODO:mask_restore Jr"),
    (re.compile(r"\bгончарни[йї]\s+трек\b", re.I), "гоночний трек", "TODO:glossary race track"),
    (re.compile(r"\bбув\s+водінням\b", re.I), "їхав", "TODO:naturalizer calque driving"),
    (re.compile(r"\bбула\s+водінням\b", re.I), "їхала", "TODO:naturalizer calque driving"),
    (re.compile(r"\bзірвати\s+війни\b", re.I), "«Зоряні війни»", "TODO:glossary Star Wars"),
    (re.compile(r"\bстаціонарн(?:ий|ому)\s+комплекс\b", re.I), "відділення інтенсивної терапії", "TODO:glossary ICU"),
    (re.compile(r"\bпрокладався\b", re.I), "лежав", "TODO:naturalizer laying"),
    (re.compile(r"\bкінематографітек\w*\b", re.I), "кінематографії", "TODO:naturalizer cinematography"),
    (re.compile(r"\bнайземніших\b", re.I), "найбільш новаторських", "TODO:naturalizer groundbreaking"),
    (re.compile(r"\bміст\b(?!\w)"), "рідне місто", "TODO:glossary hometown"),  # careful
]


@dataclass
class DirtyMTResult:
    dirty: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dirty": self.dirty,
            "dirty_mt_score": self.score,
            "reasons": list(self.reasons),
            **self.details,
        }


_EN_LEAK = re.compile(
    r"\b(dreading|actually|basically|literally|intersection|screeching|"
    r"ejected|obsession|focus|dinner|argument|hometown|intensive)\b",
    re.I,
)
_CALQUE_UK = re.compile(
    r"(не\s+міг\s+не\s+відчувати|"
    r"не\s+може\s+допомогти,\s+але\s+відчувати|"
    r"зі\s+страхом\s+очікував\s+насправді\s+отримати|"
    r"не\s+мав\s+нічого,\s+що\s+серйозно|"
    r"не\s+отримав\s+сина\s+обсесії|"
    r"ми\s+отримаємо\s+вашу\s+реальну\s+роботу|"
    r"якщо\s+він\s+прийшов\s+цей\s+величезний\s+аргумент|"
    r"справжня\s+робота|потім\s+все\s+потемніло)",
    re.I,
)
_BROKEN_NAME = re.compile(r"\b(Жр|Джер|Єр|Дад|Юра\s+Джера|Георгій\s+Жр|Георга\s+Жр)\b", re.I)
_BROKEN_PUNCT = re.compile(r",\s*\.|\.\s*,|молодш(?:ий|ого)\.\s+[а-яіїєґ]")
_GARBAGE = re.compile(
    r"гончарни|виграшного\s+приводу|США(?!\s)|був\s+водінням|зірвати\s+війни|"
    r"стаціонарн\w*\s+комплекс|прокладався|розім['']яти|"
    r"був\s+пережили|кінематографітек|"
    r"найземніш|не\s+довгати|долі\s+наради|"
    r"пішов\s+над\s+подіум|"
    r"\d+-річному\s+хлопчику|"
    r"на\s+ім['']я\s+Джорджа-молодшого|"
    r"автомобіль,\s*яка|"
    r"був\s+(?:повністю\s+)?одужав|"
    r"правий\s+мав\s+рацію|"
    r"автомобіль\s+на\s+великій\s+швидкості\s+промчала|"
    # zh→uk / Argos agreement & nonsense leftovers
    r"поколінь\s+прості|"
    r"вісім\s+поколінь\s+прості|"
    r"ви\s+товсті,\s*ви\s+вагітні|"
    r"ви\s+вагітні,\s*ви\s+товсті|"
    r"Лу\s+Ся\s+товст|"
    r"осмого\s+покоління|"
    r"осма\s+покоління|"
    r"восьме\s+покоління\s+Лу|"
    r"кілька\s+хлопців|"
    r"не\s+можу\s+думати\s+про\s+(?:таку\s+)?вагітність|"
    r"вагітна/вагітність|"
    r"ви\s+в\s+будинку,\s*ви\s+в\s+будинку|"
    r"гаряча\s+гарячка|"
    r"\bребенок\b",
    re.I,
)


def compute_dirty_mt_score(
    original: str,
    raw_mt: str,
    *,
    tgt_lang: str = "uk",
    threshold: float = 0.35,
) -> DirtyMTResult:
    """Score 0..1 how dirty the Raw MT is. dirty=True when score >= threshold."""
    src = str(original or "").strip()
    tr = str(raw_mt or "").strip()
    reasons: list[str] = []
    score = 0.0
    details: dict[str, Any] = {}

    if not tr:
        return DirtyMTResult(True, 1.0, ["empty"], {"empty": True})

    # English leak on UK/RU target
    lang = (tgt_lang or "uk").split("-")[0].lower()
    if lang in ("uk", "ru", "be"):
        letters = [c for c in tr if c.isalpha()]
        if letters:
            latin = sum(1 for c in letters if "a" <= c.lower() <= "z")
            latin_pct = latin / len(letters)
            details["latin_pct"] = round(latin_pct, 3)
            if latin_pct > 0.35 and len(letters) > 10:
                score += 0.35
                reasons.append("english_leak")
        if _EN_LEAK.search(tr):
            score += 0.25
            reasons.append("en_word_leak")

    if _BROKEN_NAME.search(tr):
        score += 0.4
        reasons.append("entity_breakage")
    if _BROKEN_PUNCT.search(tr):
        score += 0.2
        reasons.append("broken_punct")
    if _GARBAGE.search(tr):
        score += 0.4
        reasons.append("nonsense_calque")
    if _CALQUE_UK.search(tr):
        score += 0.25
        reasons.append("calque")

    # Source script still in "translation" (any pair)
    try:
        from engines.mt.cross_script_guard import (
            has_phrase_loop,
            meaning_collapse,
            source_script_leak,
        )

        leak = source_script_leak(src, tr, source_lang=None, target_lang=lang)
        if leak:
            score += 0.55
            reasons.append("source_script_leak")
            details["leak"] = leak
        collapse = meaning_collapse(src, tr, source_lang=None, target_lang=lang)
        if collapse:
            score += 0.55
            reasons.append("meaning_collapse")
            reasons.append("cjk_meaning_collapse")  # review / legacy warning code
            details["collapse"] = {k: collapse[k] for k in ("reasons", "meta_waffle") if k in collapse}
        if has_phrase_loop(tr, min_repeats=3):
            score += 0.5
            reasons.append("phrase_loop")
            details["phrase_loop"] = True
    except Exception:
        pass

    # Severe collapse
    try:
        from engines.mt.sentence_split import is_severe_mt_collapse

        if is_severe_mt_collapse(src, tr):
            score += 0.45
            reasons.append("mt_collapse")
    except Exception:
        pass

    # hometown → міст (wrong short form) when source has hometown
    if re.search(r"\bhometown\b", src, re.I) and re.search(
        r"(?<![а-яіїєґ])міст(?![а-яіїєґ])", tr, re.I
    ):
        if "рідне" not in tr.lower():
            score += 0.35
            reasons.append("hometown_mist")

    # USC → США wrong expansion (flag even if another USC token exists)
    if re.search(r"\bUSC\b", src) and re.search(r"\bСША\b", tr):
        score += 0.4
        reasons.append("usc_as_usa")

    score = min(1.0, score)
    return DirtyMTResult(
        dirty=score >= threshold,
        score=round(score, 3),
        reasons=sorted(set(reasons)),
        details=details,
    )


def apply_temporary_entity_repair(text: str) -> tuple[str, list[str]]:
    """Temporary regex repair layer — delegates to TRH canon_repair."""
    try:
        from engines.trh.canon_repair import apply_canon_repair

        return apply_canon_repair(text)
    except Exception:
        pass
    out = str(text or "")
    applied: list[str] = []
    for pat, repl, ticket in _TEMP_ENTITY_REPAIRS:
        if pat.search(out):
            if "hometown" in ticket and "рідне місто" in out.lower():
                continue
            if "hometown" in ticket:
                new = re.sub(
                    r"(?<![а-яіїєґ])міст(?![а-яіїєґо])", "рідне місто", out, flags=re.I
                )
            else:
                new = pat.sub(repl, out)
            if new != out:
                out = new
                applied.append(ticket)
    return out, applied


def residual_dirty_after_naturalize(
    original: str,
    naturalized: str,
    *,
    tgt_lang: str = "uk",
) -> bool:
    """True when the post-naturalizer text is still dirty / calqued."""
    if compute_dirty_mt_score(original, naturalized, tgt_lang=tgt_lang).dirty:
        return True
    try:
        from engines.naturalizer_v2.bad_patterns import has_bad_mt

        if has_bad_mt(naturalized):
            return True
    except Exception:
        pass
    return False


def naturalizer_noop_is_bug(
    original: str,
    raw_mt: str,
    naturalized: str,
    *,
    tgt_lang: str = "uk",
) -> bool:
    """Dirty MT left unfixed (exact noop OR residual calques after polish)."""
    raw = " ".join(str(raw_mt or "").split())
    nat = " ".join(str(naturalized or "").split())
    # Cosmetic entity tweaks must not hide residual garbage
    if residual_dirty_after_naturalize(original, naturalized, tgt_lang=tgt_lang):
        return True
    if raw != nat:
        return False
    return compute_dirty_mt_score(original, raw_mt, tgt_lang=tgt_lang).dirty
