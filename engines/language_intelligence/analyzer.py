"""Issue detection — answers: does this sound like natural target language?"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from engines.language_intelligence import rules as R


@dataclass
class AnalysisResult:
    natural: bool
    issues: list[dict[str, Any]] = field(default_factory=list)
    score: float = 100.0

    def add(self, code: str, detail: str = "", *, tokens: list[str] | None = None) -> None:
        self.issues.append({"code": code, "detail": detail, "tokens": tokens or []})
        self.natural = False


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", str(text or ""), flags=re.UNICODE))


def _duplicate_words(text: str) -> list[str]:
    words = re.findall(r"\b[\w'-]+\b", str(text or "").lower(), flags=re.UNICODE)
    dups: list[str] = []
    for i in range(1, len(words)):
        if words[i] == words[i - 1] and len(words[i]) > 2:
            dups.append(words[i])
    return dups


def _detect_calques(text: str, tgt_lang: str) -> list[str]:
    if tgt_lang.split("-")[0] != "uk":
        return []
    hits: list[str] = []
    for pat, _, cat in R.UK_CALQUE_RULES:
        if cat == "calque" and re.search(pat, text, re.I):
            hits.append(pat)
    return hits


def analyze_segment(
    *,
    original: str,
    raw_mt: str,
    naturalized: str,
    final: str,
    src_lang: str = "en",
    tgt_lang: str = "uk",
) -> AnalysisResult:
    """Analyze one segment; natural=True means no changes needed."""
    text = str(final or naturalized or raw_mt or "").strip()
    result = AnalysisResult(natural=True, score=100.0)

    if not text:
        result.add("empty", "empty final text")
        result.score = 0.0
        return result

    tgt = tgt_lang.split("-")[0]

    ru_hits = R.detect_russian_words(text, tgt_lang)
    if ru_hits:
        result.add("russian_in_ukrainian", "Russian words in Ukrainian output", tokens=ru_hits)
        result.score -= min(40.0, len(ru_hits) * 12.0)

    en_hits = R.detect_english_leak(text, original, tgt_lang)
    if en_hits:
        result.add("english_untranslated", "English words that should be translated", tokens=en_hits)
        result.score -= min(25.0, len(en_hits) * 8.0)

    calques = _detect_calques(text, tgt_lang)
    if calques:
        result.add("calque", "Literal calque detected", tokens=calques[:5])
        result.score -= min(20.0, len(calques) * 10.0)

    dups = _duplicate_words(text)
    if dups:
        result.add("repetition", "Repeated adjacent words", tokens=dups[:5])
        result.score -= min(10.0, len(dups) * 4.0)

    cyr = len(re.findall(r"[а-яА-ЯёЁіїєІЇЄ]", text))
    lat = len(re.findall(r"[a-zA-Z]", text))
    if tgt in ("uk", "ru") and lat > 0 and cyr > 0:
        mix_pct = lat / max(cyr + lat, 1) * 100.0
        if mix_pct > 15:
            result.add("mixed_language", f"Latin/Cyrillic mix ~{mix_pct:.0f}%")
            result.score -= min(15.0, mix_pct * 0.3)

    if tgt == "uk":
        for latin, bad_list in R.CYRILLIC_MISTRANSLATIONS.items():
            if latin.lower() in (original or "").lower():
                if not re.search(re.escape(latin), text, re.I):
                    for bad in bad_list:
                        if bad.lower() in text.lower():
                            result.add("wrong_brand", f"{latin} → keep Latin or preferred UA", tokens=[bad])
                            result.score -= 10.0
                            break

        for src_title, ua_title in R.PREFERRED_UA_TITLES.items():
            if src_title.lower() in (original or "").lower():
                if ua_title.lower() not in text.lower() and src_title.lower() in text.lower():
                    result.add("wrong_title", f"{src_title} should be {ua_title}", tokens=[src_title])

    wc = _word_count(text)
    ow = _word_count(original)
    if ow > 3 and wc > ow * 2.2:
        result.add("over_expansion", "Suspicious length vs original")
        result.score -= 8.0
    if ow > 5 and wc < ow * 0.35:
        result.add("under_translation", "Too short vs original")
        result.score -= 10.0

    if text.lower() == (original or "").lower() and src_lang.split("-")[0] != tgt:
        result.add("untranslated", "Final equals original")
        result.score = min(result.score, 35.0)

    result.score = max(0.0, min(100.0, round(result.score, 1)))
    if result.score >= 85.0 and not result.issues:
        result.natural = True
    elif result.score >= 92.0 and len(result.issues) <= 1:
        result.natural = True
    else:
        result.natural = len(result.issues) == 0

    return result
