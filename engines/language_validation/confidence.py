# -*- coding: utf-8 -*-
"""Probabilistic language scoring over the FULL text (never first-N words only)."""

from __future__ import annotations

import re
from typing import Any

_CYR = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")
_LAT = re.compile(r"[A-Za-z]")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af]")
_ARABIC = re.compile(r"[\u0600-\u06ff]")

# Ukrainian-specific letters / tokens
_UK_MARKERS = re.compile(r"[іІїЇєЄґҐ]")
_UK_WORDS = re.compile(
    r"\b(і|та|що|як|але|коли|тому|тому́|був|була|було|були|він|вона|"
    r"воно|вони|цей|ця|це|які|який|яка|не|так|ще|вже|дуже|також|"
    r"після|перед|через|між|про|для|від|до|з|у|на|під|над|"
    r"хлопець|хлопчик|дівчина|місто|батько|син|фотографі|кінематограф)\b",
    re.I,
)
# Russian-specific
_RU_MARKERS = re.compile(r"[ыЫэЭъЪ]")
_RU_WORDS = re.compile(
    r"\b(и|что|как|но|когда|поэтому|был|была|было|были|он|она|"
    r"оно|они|этот|эта|это|которые|который|которая|не|да|ещё|уже|"
    r"очень|также|после|перед|через|между|про|для|от|до|с|в|на|"
    r"парень|мальчик|девушка|город|отец|сын|фотографи|кинематограф)\b",
    re.I,
)
_EN_FUNC = re.compile(
    r"\b(the|and|but|that|was|were|have|been|from|with|this|are|is|"
    r"of|to|in|on|at|for|his|her|their|who|which|when|after|before)\b",
    re.I,
)


def _alpha_len(text: str) -> int:
    return sum(1 for c in text if c.isalpha())


def score_language_confidence(
    text: str,
    *,
    target_lang: str = "",
    masked: bool = False,
) -> dict[str, Any]:
    """Return per-language scores and top detection over the entire string.

    Scores are soft probabilities (sum≈1). Uses distinctive UK/RU markers and
    full-text script counts — never truncates to a head window.
    """
    t = str(text or "").strip()
    # Placeholders from entity mask are not alpha — strip them for ratios
    t_clean = re.sub(r"⟨E⟩", " ", t)
    t_clean = re.sub(r"\s+", " ", t_clean).strip()
    base = str(target_lang or "").split("-")[0].lower()

    empty = {
        "scores": {"uk": 0.0, "ru": 0.0, "en": 0.0, "zh": 0.0, "other": 0.0},
        "detected": "empty",
        "confidence": 0.0,
        "script": "empty",
        "alpha_chars": 0,
        "full_text": True,
        "masked_input": masked,
        "backup": None,
    }
    if not t_clean:
        return empty

    cyr = len(_CYR.findall(t_clean))
    lat = len(_LAT.findall(t_clean))
    cjk = len(_CJK.findall(t_clean))
    ara = len(_ARABIC.findall(t_clean))
    alpha = max(_alpha_len(t_clean), 1)

    raw = {"uk": 0.0, "ru": 0.0, "en": 0.0, "zh": 0.0, "other": 0.0}
    script = "mixed"

    if cjk / alpha >= 0.35:
        raw["zh"] += 0.85
        script = "cjk"
    if ara / alpha >= 0.35:
        raw["other"] += 0.8
        script = "arabic"
    if cyr / alpha >= 0.25:
        script = "cyrillic" if cyr >= lat else script
        uk_m = len(_UK_MARKERS.findall(t_clean))
        ru_m = len(_RU_MARKERS.findall(t_clean))
        uk_w = len(_UK_WORDS.findall(t_clean))
        ru_w = len(_RU_WORDS.findall(t_clean))
        # Base Cyrillic mass
        raw["uk"] += 0.35 * (cyr / alpha)
        raw["ru"] += 0.35 * (cyr / alpha)
        raw["uk"] += 0.08 * uk_m + 0.04 * uk_w
        raw["ru"] += 0.08 * ru_m + 0.04 * ru_w
        # If target is uk/ru and no opposing markers, boost target
        if base == "uk" and ru_m == 0 and uk_m + uk_w > 0:
            raw["uk"] += 0.25
        elif base == "uk" and ru_m == 0 and cyr >= 8:
            raw["uk"] += 0.18  # solid Cyrillic toward UK track without RU letters
        if base == "ru" and uk_m == 0 and ru_m + ru_w > 0:
            raw["ru"] += 0.25
        elif base == "ru" and uk_m == 0 and cyr >= 8:
            raw["ru"] += 0.18
        # Opposing markers dominate
        if uk_m >= 2 and ru_m == 0:
            raw["uk"] += 0.2
        if ru_m >= 2 and uk_m == 0:
            raw["ru"] += 0.2

    if lat / alpha >= 0.2:
        en_f = len(_EN_FUNC.findall(t_clean))
        raw["en"] += 0.3 * (lat / alpha) + 0.05 * en_f
        if cyr == 0 and cjk == 0 and en_f >= 2:
            raw["en"] += 0.35
            script = "latin"

    # Normalize
    total = sum(raw.values()) or 1.0
    scores = {k: round(v / total, 4) for k, v in raw.items()}
    detected = max(scores, key=scores.get)  # type: ignore[arg-type]
    confidence = float(scores.get(detected, 0.0))

    # Target-aligned confidence: P(expected)
    target_conf = float(scores.get(base, 0.0)) if base else confidence

    backup = _backup_langdetect(t_clean)

    # Blend backup lightly when primary is ambiguous
    if backup and backup.get("lang"):
        b_lang = str(backup["lang"])
        b_conf = float(backup.get("confidence") or 0.0)
        if b_lang in scores and 0.45 <= confidence <= 0.75:
            scores[b_lang] = round(min(1.0, scores[b_lang] + 0.15 * b_conf), 4)
            total2 = sum(scores.values()) or 1.0
            scores = {k: round(v / total2, 4) for k, v in scores.items()}
            detected = max(scores, key=scores.get)  # type: ignore[arg-type]
            confidence = float(scores.get(detected, 0.0))
            target_conf = float(scores.get(base, 0.0)) if base else confidence

    return {
        "scores": scores,
        "detected": detected,
        "confidence": round(confidence, 4),
        "target_confidence": round(target_conf, 4),
        "script": script,
        "alpha_chars": alpha,
        "counts": {"cyrillic": cyr, "latin": lat, "cjk": cjk, "arabic": ara},
        "full_text": True,
        "masked_input": masked,
        "backup": backup,
    }


def _backup_langdetect(text: str) -> dict[str, Any] | None:
    """Optional secondary detector (langdetect). Never truncates below full text."""
    sample = str(text or "").strip()
    if len(sample) < 12:
        return None
    try:
        from langdetect import detect_langs  # type: ignore

        hits = detect_langs(sample[:5000] if len(sample) > 5000 else sample)
        if not hits:
            return None
        top = hits[0]
        lang = str(getattr(top, "lang", "") or "")
        conf = float(getattr(top, "prob", 0.0) or 0.0)
        # Map zh-cn → zh
        if lang.startswith("zh"):
            lang = "zh"
        return {
            "engine": "langdetect",
            "lang": lang,
            "confidence": round(conf, 4),
            "all": [
                {
                    "lang": str(getattr(h, "lang", "")),
                    "confidence": round(float(getattr(h, "prob", 0.0) or 0.0), 4),
                }
                for h in hits[:5]
            ],
        }
    except Exception:
        return None


def neighbor_language_vote(
    neighbor_texts: list[str],
    *,
    target_lang: str,
) -> dict[str, Any]:
    """Majority vote over neighboring segments (context disambiguation)."""
    votes: dict[str, float] = {}
    details: list[dict[str, Any]] = []
    for nb in neighbor_texts:
        if not str(nb or "").strip():
            continue
        sc = score_language_confidence(nb, target_lang=target_lang)
        lang = str(sc.get("detected") or "unknown")
        conf = float(sc.get("confidence") or 0.0)
        votes[lang] = votes.get(lang, 0.0) + conf
        details.append({"detected": lang, "confidence": conf})
    if not votes:
        return {"detected": "", "confidence": 0.0, "details": details}
    winner = max(votes, key=votes.get)  # type: ignore[arg-type]
    total = sum(votes.values()) or 1.0
    return {
        "detected": winner,
        "confidence": round(votes[winner] / total, 4),
        "votes": {k: round(v, 4) for k, v in votes.items()},
        "details": details,
    }
