"""MT quality scoring (0–100) — language-agnostic metrics for Translation Router."""

from __future__ import annotations

import re
from typing import Any

_LATIN_RE = re.compile(r"[a-zA-Z]")
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁіїєІЇЄ]")
_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_EN_WORD_RE = re.compile(r"\b[a-zA-Z]{2,}\b")

# Target langs expected to use Cyrillic script in dub output
_CYRILLIC_TARGETS = frozenset(
    {"ru", "uk", "bg", "sr", "mk", "be", "kk", "mn"}
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

_CONJUNCTION_SPLIT = frozenset(
    {
        "and", "but", "because", "so", "when", "while", "if", "after", "before",
        "or", "although", "though", "since", "until", "unless",
    }
)

MIN_ACCEPT_QUALITY = 55.0
MIN_MT_FOR_NATURAL = 0.0
MIN_QUALITY_GOOD = 70.0

# Russian words that should not appear in Ukrainian dub output
_UK_RUISM_WORDS = frozenset(
    {
        "что", "этот", "эта", "это", "эти", "они", "его", "её", "ее", "нет",
        "чтобы", "ещё", "еще", "который", "которая", "которое", "которые",
        "тоже", "также", "сейчас", "потому", "тогда", "здесь", "там",
        "очень", "может", "будет", "был", "была", "были", "быть",
        "только", "когда", "если", "или", "но", "да", "нет",
    }
)


def _uk_ruism_hits(text: str) -> list[str]:
    words = [w.lower() for w in _WORD_RE.findall(str(text or ""))]
    return sorted({w for w in words if w in _UK_RUISM_WORDS})


def _norm_lang(code: str | None) -> str:
    return (code or "en").split("-")[0].lower()


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or ""))


def compute_quality_metrics(
    original: str,
    translated: str,
    *,
    src_lang: str | None,
    tgt_lang: str | None,
) -> dict[str, Any]:
    """Detailed metrics used by Router and Translation Review."""
    src = _norm_lang(src_lang)
    tgt = _norm_lang(tgt_lang)
    orig = str(original or "").strip()
    tr = str(translated or "").strip()

    orig_words = _words(orig)
    tr_words = _words(tr)
    word_count = len(tr_words)

    from engines.translation_quality import extract_preserved_tokens

    preserved = {
        re.sub(r"\s+", "", t.lower())
        for t in extract_preserved_tokens(orig)
    }
    orig_ascii = {
        w.lower()
        for w in orig_words
        if len(w) > 2
        and w.isascii()
        and _EN_WORD_RE.fullmatch(w)
        and re.sub(r"\s+", "", w.lower()) not in preserved
    }
    source_leak_words = [
        w
        for w in tr_words
        if w.lower() in orig_ascii and _EN_WORD_RE.fullmatch(w)
    ]
    source_leak_pct = (len(source_leak_words) / max(len(tr_words), 1)) * 100.0

    latin_in_cyrillic = [
        w
        for w in tr_words
        if _EN_WORD_RE.fullmatch(w)
        and re.sub(r"\s+", "", w.lower()) not in preserved
    ]
    english_word_pct = source_leak_pct
    english_long = [w for w in source_leak_words if len(w) > 3]
    if tgt in _CYRILLIC_TARGETS:
        english_word_pct = (len(latin_in_cyrillic) / max(len(tr_words), 1)) * 100.0
        english_long = [w for w in latin_in_cyrillic if len(w) > 3 and tgt != "en"]

    untranslated = len(source_leak_words) if src != tgt else 0
    untranslated_pct = (untranslated / max(len(orig_words), 1)) * 100.0

    cyrillic_chars = len(_CYRILLIC_RE.findall(tr))
    latin_chars = len(_LATIN_RE.findall(tr))
    total_alpha = cyrillic_chars + latin_chars + 1
    cyrillic_pct = (cyrillic_chars / total_alpha) * 100.0
    no_cyrillic_pct = 100.0 - cyrillic_pct if tgt in _CYRILLIC_TARGETS else 0.0

    mixed_language_pct = 0.0
    if tgt in _CYRILLIC_TARGETS and latin_chars > 0 and cyrillic_chars > 0:
        mixed_language_pct = (latin_chars / total_alpha) * 100.0
    elif tgt not in _CYRILLIC_TARGETS and tgt != "en" and source_leak_pct > 5:
        mixed_language_pct = source_leak_pct

    raw_equals_whisper = bool(
        orig and tr and orig.lower() == tr.lower() and src != tgt
    )

    from engines.translation_quality import (
        missing_preserved_tokens,
        segment_quality_warnings,
        validate_raw_mt,
    )

    preserved_missing = missing_preserved_tokens(orig, tr)
    from engines.placeholder_guard import detect_placeholder_leaks, has_cjk_garbage

    placeholder_leaks = detect_placeholder_leaks(tr)
    placeholder_leak_count = len(placeholder_leaks)
    cjk_garbage = has_cjk_garbage(tr)
    cjk_meaning_collapse = False
    meaning_collapse_hit = False
    try:
        from engines.mt.cross_script_guard import meaning_collapse

        collapse = meaning_collapse(
            orig, tr, source_lang=src, target_lang=tgt
        )
        if collapse:
            meaning_collapse_hit = True
            if collapse.get("source_script") == "cjk" or (src or "").startswith(
                ("zh", "ja", "ko")
            ):
                cjk_meaning_collapse = True
            else:
                cjk_meaning_collapse = True  # score path uses this flag generically
    except Exception:
        pass
    from engines.mt.qe import runtime_qe_penalties

    qe = runtime_qe_penalties(orig, tr, src_lang=src, tgt_lang=tgt)
    raw_issues = validate_raw_mt(orig, tr, source_lang=src, target_lang=tgt)
    warnings = segment_quality_warnings(
        original=orig,
        raw=tr,
        naturalized=tr,
        final=tr,
        tts_text=tr,
        source_lang=src,
        target_lang=tgt,
    )
    preserved_warnings = sum(1 for w in warnings if w.get("code") == "preserved_token")

    translated_pct = 100.0
    if orig and tr and not raw_equals_whisper:
        translated_pct = max(0.0, 100.0 - untranslated_pct - english_word_pct * 0.5)

    return {
        "word_count": word_count,
        "english_word_count": len(latin_in_cyrillic if tgt in _CYRILLIC_TARGETS else source_leak_words),
        "english_long_count": len(english_long),
        "source_leak_pct": round(source_leak_pct, 2),
        "english_word_pct": round(english_word_pct, 2),
        "untranslated_token_pct": round(untranslated_pct, 2),
        "no_cyrillic_pct": round(no_cyrillic_pct, 2),
        "mixed_language_pct": round(mixed_language_pct, 2),
        "translated_pct": round(translated_pct, 2),
        "raw_equals_whisper": raw_equals_whisper,
        "preserved_token_warnings": preserved_warnings,
        "warning_count": len(warnings),
        "raw_issues": raw_issues,
        "missing_preserved_tokens": qe["missing_preserved_tokens"],
        "wrongful_substitutions": qe["wrongful_substitutions"],
        "placeholder_leaks": placeholder_leaks,
        "placeholder_leak_count": placeholder_leak_count,
        "cjk_garbage": cjk_garbage,
        "cjk_meaning_collapse": cjk_meaning_collapse,
        "meaning_collapse": meaning_collapse_hit,
        "length_ratio_penalty": qe["length_ratio_penalty"],
        "intro_pattern_penalty": qe.get("intro_pattern_penalty", 0.0),
        "qe_penalty": qe["qe_penalty"],
    }


def compute_quality_score(
    original: str,
    translated: str,
    *,
    src_lang: str | None,
    tgt_lang: str | None,
) -> tuple[float, dict[str, Any]]:
    """Quality Score 0–100 from MT metrics."""
    from engines.proper_nouns_dict import wrong_phonetic_brand_hits
    from engines.semantic_translation import detect_semantic_issues

    metrics = compute_quality_metrics(
        original, translated, src_lang=src_lang, tgt_lang=tgt_lang
    )
    score = 100.0

    if not str(translated or "").strip():
        return 0.0, {**metrics, "quality_score": 0.0}

    if metrics["raw_equals_whisper"]:
        score -= 35.0
    tgt = _norm_lang(tgt_lang)
    if tgt in _CYRILLIC_TARGETS:
        score -= metrics["english_word_pct"] * 0.85
        score -= metrics["mixed_language_pct"] * 0.9
        if metrics["no_cyrillic_pct"] > 40:
            score -= metrics["no_cyrillic_pct"] * 0.4
    else:
        score -= metrics["untranslated_token_pct"] * 0.8
        if metrics["source_leak_pct"] > 20:
            score -= (metrics["source_leak_pct"] - 20) * 0.5
    score -= metrics["preserved_token_warnings"] * 6
    score -= metrics.get("qe_penalty", 0.0)
    score -= len(metrics["raw_issues"]) * 10
    score -= metrics["warning_count"] * 2

    leak_n = int(metrics.get("placeholder_leak_count") or 0)
    if leak_n:
        score -= min(60.0, leak_n * 25.0)
        score = min(score, 35.0)

    wc = metrics["word_count"]
    if wc > 22:
        score -= min(15.0, (wc - 22) * 0.8)

    for issue in detect_semantic_issues(
        original, translated, source_lang=src_lang, target_lang=tgt_lang
    ):
        code = issue.get("code", "")
        if code in ("literal_construction", "idiom"):
            score -= 12.0
        elif code:
            score -= 6.0

    if tgt == "uk":
        ru_hits = _uk_ruism_hits(translated)
        score -= min(36.0, len(ru_hits) * 12.0)
        metrics["uk_ruism_hits"] = ru_hits

    brand_hits = wrong_phonetic_brand_hits(original, translated)
    if brand_hits:
        score -= min(25.0, len(brand_hits) * 10.0)
        metrics["wrong_brand_translations"] = brand_hits

    from engines.proper_nouns_dict import wrong_title_hits

    title_hits = wrong_title_hits(original, translated)
    if title_hits:
        score -= min(20.0, len(title_hits) * 8.0)
        metrics["wrong_title_translations"] = title_hits

    if metrics.get("uk_ruism_hits"):
        score = min(score, max(45.0, 100.0 - len(metrics["uk_ruism_hits"]) * 12.0))

    semantic_issues = detect_semantic_issues(
        original, translated, source_lang=src_lang, target_lang=tgt_lang
    )
    metrics["semantic_issues"] = len(semantic_issues)

    if metrics.get("raw_equals_whisper"):
        score = min(score, 40.0)
    if metrics.get("english_word_pct", 0) > 25:
        score = min(score, 55.0)
    if metrics.get("mixed_language_pct", 0) > 20:
        score = min(score, 50.0)
    if brand_hits:
        score = min(score, 60.0)
    if title_hits:
        score = min(score, 65.0)
    if metrics.get("semantic_issues", 0) > 0:
        score = min(score, 75.0)
    if metrics.get("placeholder_leak_count", 0) > 0:
        score = 0.0

    if metrics.get("cjk_garbage"):
        score = 0.0
    if metrics.get("cjk_meaning_collapse") or metrics.get("meaning_collapse"):
        # Cross-script / hallucinated MT — never look like a good score
        score = min(score, 18.0)
        score -= 40.0

    if tgt in _CYRILLIC_TARGETS and _CJK_RE.search(str(translated or "")):
        metrics["cjk_garbage"] = True
        score = min(score, 0.0)

    score = max(0.0, min(100.0, round(score, 2)))
    metrics["quality_score"] = score
    return score, metrics


def should_switch_route(score: float, metrics: dict[str, Any]) -> bool:
    """True when Router must try a fallback route."""
    if score < MIN_ACCEPT_QUALITY:
        return True
    if metrics.get("mixed_language_pct", 0) > 12:
        return True
    if metrics.get("english_word_pct", 0) > 15 and metrics.get("english_long_count", 0) > 0:
        return True
    if metrics.get("source_leak_pct", 0) > 35:
        return True
    if metrics.get("missing_preserved_tokens", 0) > 0:
        return True
    if metrics.get("wrongful_substitutions", 0) > 0:
        return True
    if metrics.get("intro_pattern_penalty", 0) > 0:
        return True
    if metrics.get("length_ratio_penalty", 0) > 12:
        return True
    if metrics.get("raw_equals_whisper"):
        return True
    if metrics.get("placeholder_leak_count", 0) > 0:
        return True
    if metrics.get("no_cyrillic_pct", 0) > 55:
        return True
    if metrics.get("cjk_garbage") or metrics.get("cjk_meaning_collapse") or metrics.get("meaning_collapse"):
        return True
    return False


def mt_quality_ok_for_natural(score: float) -> bool:
    """Naturalizer always runs (rule-based); score used for extra LLM effort."""
    return True


def needs_aggressive_natural(score: float) -> bool:
    """Score below 70 — mandatory improvement attempt (rules + optional LLM)."""
    return float(score) < MIN_QUALITY_GOOD
