"""Style analysis — naturalness score inputs without applying fixes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from engines.language_intelligence import rules as R


@dataclass
class StyleAnalysis:
    naturalness_score: float = 100.0
    issues: list[dict[str, Any]] = field(default_factory=list)
    objective_issue: bool = False

    def penalize(self, amount: float, code: str, detail: str = "", **extra: Any) -> None:
        self.naturalness_score -= amount
        self.issues.append({"code": code, "detail": detail, **extra})
        self.objective_issue = True


def analyze_style(
    *,
    original: str,
    raw_mt: str,
    naturalized: str,
    final: str,
    src_lang: str = "en",
    tgt_lang: str = "uk",
) -> StyleAnalysis:
    text = str(final or naturalized or raw_mt or "").strip()
    result = StyleAnalysis(naturalness_score=100.0)

    if not text:
        result.penalize(100, "empty")
        result.naturalness_score = 0.0
        return result

    ru = R.detect_russian_words(text, tgt_lang)
    if ru:
        result.penalize(min(55, len(ru) * 20), "ruism", tokens=ru)

    en = R.detect_english_leak(text, original, tgt_lang)
    if en:
        result.penalize(min(30, len(en) * 10), "english_leak", tokens=en)

    if tgt_lang.split("-")[0] == "uk":
        for pat, _, cat in R.UK_CALQUE_RULES:
            if re.search(pat, text, re.I):
                result.penalize(14, "calque", pattern=pat, category=cat)

        orig_l = str(original or "")
        for latin in R.KEEP_LATIN:
            if not re.search(r"(?<!\w)" + re.escape(latin) + r"(?!\w)", orig_l, re.I):
                continue
            for bad in R.CYRILLIC_MISTRANSLATIONS.get(latin, []):
                if bad.lower() in text.lower():
                    result.penalize(18, "brand_mistranslation", token=latin)
                    break

        for src_title, ua_title in R.PREFERRED_UA_TITLES.items():
            if not re.search(r"(?<!\w)" + re.escape(src_title) + r"(?!\w)", orig_l, re.I):
                continue
            if ua_title.lower() in text.lower():
                continue
            if re.search(r"(?<!\w)" + re.escape(src_title) + r"(?!\w)", text, re.I):
                result.penalize(22, "title_untranslated", token=src_title)

    words = re.findall(r"\b[\w'-]+\b", text.lower())
    for i in range(1, len(words)):
        if words[i] == words[i - 1] and len(words[i]) > 2:
            result.penalize(6, "repetition", token=words[i])

    cyr = len(re.findall(r"[а-яА-ЯёЁіїєІЇЄ]", text))
    lat = len(re.findall(r"[a-zA-Z]", text))
    if tgt_lang.split("-")[0] in ("uk", "ru") and lat > 0 and cyr > 0:
        mix = lat / max(cyr + lat, 1) * 100
        if mix > 12:
            result.penalize(mix * 0.25, "mixed_language", pct=round(mix, 1))

    if text.lower() == (original or "").lower() and src_lang.split("-")[0] != tgt_lang.split("-")[0]:
        result.penalize(40, "untranslated")

    result.naturalness_score = max(0.0, min(100.0, round(result.naturalness_score, 1)))
    return result
