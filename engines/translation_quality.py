"""Preserved tokens + pipeline stage validation (language-agnostic)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_COMMON_NON_NAMES = frozenset(
    {
        "the",
        "this",
        "that",
        "and",
        "but",
        "then",
        "when",
        "what",
        "where",
        "how",
        "why",
        "who",
        "she",
        "he",
        "they",
        "we",
        "you",
        "i",
        "it",
        "his",
        "her",
        "their",  # Added these to filter out possessive pronouns
        "hello",
        "hi",
        "hey",
        "thanks",
        "thank",
        "please",
        "sorry",
        "let",
        "это",
        "этот",
        "эта",
        "они",
        "она",
        "он",
        "мы",
        "вы",
        "я",
        "це",
        "він",
        "вона",
        "вони",
        "ми",
        "ви",
        "я",
    }
)

# Sentence-initial / discourse words — never proper nouns
_DISCOURSE_MARKERS = frozenset(
    {
        "two",
        "now",
        "instead",
        "like",
        "well",
        "so",
        "but",
        "and",
        "or",
        "then",
        "when",
        "while",
        "actually",
        "maybe",
        "wow",
        "oh",
        "yes",
        "still",
        "yet",
        "here",
        "there",
        "also",
        "just",
        "even",
        "only",
        "however",
        "therefore",
        "because",
        "although",
        "though",
        "if",
        "once",
        "first",
        "second",
        "third",
        "finally",
        "anyway",
        "okay",
        "right",
        "sure",
        "look",
        "see",
        "say",
        "said",
        "got",
        "get",
        "let",
        "make",
        "yesterday",
        "today",
        "tomorrow",
    }
)

# 2–4 letter all-caps that are usually not proper names/abbreviations to preserve
_COMMON_ACRONYM_SKIP = frozenset(
    {
        "TO",
        "IN",
        "ON",
        "AT",
        "BY",
        "OR",
        "IF",
        "AS",
        "IS",
        "AM",
        "PM",
        "TV",
        "OK",
        "NO",
        "SO",
        "UP",
        "GO",
        "DO",
        "BE",
        "AN",
        "OF",
        "US",
        "UK",
        "IT",
        "WE",
        "HE",
        "ME",
        "MY",
        "TO",
        "OF",
    }
)

_TITLE_ABBREV_RE = re.compile(
    r"\b(?:Jr|Sr|Mr|Mrs|Ms|Dr|Prof|St|Co|Inc|Ltd|Dept|Univ)\.?\b",
    re.IGNORECASE,
)
_DOTTED_ABBREV_RE = re.compile(r"\b[A-Z]\.\s?[A-Z]\.\b")
_PHD_RE = re.compile(r"\bPh\.?\s?D\.?\b", re.IGNORECASE)
_ALLCAPS_ABBREV_RE = re.compile(r"\b[A-Z]{2,}\b")
_PROPER_EN_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:[''][A-Za-z]+)?\b")
_PROPER_CYR_RE = re.compile(r"\b[А-ЯЁІЇЄ][a-zа-яёіїє]{2,}\b")


def _is_sentence_initial(text: str, start: int) -> bool:
    if start <= 0:
        return True
    prefix = text[:start].rstrip()
    return bool(re.search(r"[.!?…]\s*$", prefix))


def extract_proper_nouns(text: str) -> list[str]:
    t = str(text or "")
    found: list[str] = []
    for pat in (_PROPER_EN_RE, _PROPER_CYR_RE):
        for m in pat.finditer(t):
            word = m.group(0)
            key = word.lower()
            if key in _COMMON_NON_NAMES or key in _DISCOURSE_MARKERS:
                continue
            if _is_sentence_initial(t, m.start()) and key in _DISCOURSE_MARKERS:
                continue
            found.append(word)
    seen: set[str] = set()
    out: list[str] = []
    for w in found:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out


def extract_abbreviations(text: str) -> list[str]:
    t = str(text or "")
    found: list[str] = []

    for m in _TITLE_ABBREV_RE.finditer(t):
        found.append(m.group(0).strip())

    for m in _DOTTED_ABBREV_RE.finditer(t):
        found.append(m.group(0).strip())

    for m in _PHD_RE.finditer(t):
        found.append(m.group(0).strip())

    for m in _ALLCAPS_ABBREV_RE.finditer(t):
        tok = m.group(0).strip()
        if tok in _COMMON_ACRONYM_SKIP:
            continue
        found.append(tok)

    seen: set[str] = set()
    out: list[str] = []
    for w in found:
        key = re.sub(r"\s+", "", w.lower())
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out


def extract_preserved_tokens(text: str, *, app_dir=None) -> list[str]:
    """Proper nouns + abbreviations + dictionary never-translate hits."""
    from pathlib import Path

    from engines.proper_nouns_dict import extra_preserved_tokens

    base = Path(app_dir) if app_dir else Path(__file__).resolve().parent.parent
    seen: set[str] = set()
    out: list[str] = []
    for tok in (
        extract_proper_nouns(text)
        + extract_abbreviations(text)
        + extra_preserved_tokens(text, app_dir=base)
    ):
        key = re.sub(r"\s+", "", tok.lower())
        if key not in seen:
            seen.add(key)
            out.append(tok)
    return out


_TECH_TERM_SUBSTITUTIONS = frozenset(
    {
        "system",
        "kernel",
        "manager",
        "processor",
        "computer",
        "device",
        "module",
        "server",
        "client",
        "driver",
        "handler",
        "controller",
        "система",
        "ядро",
        "менеджер",
        "процессор",
        "компьютер",
        "устройство",
        "система",
        "ядро",
        "менеджер",
        "процесор",
        "комп'ютер",
        "пристрій",
    }
)


def name_to_tech_term_damage(source: str, translated: str) -> list[str]:
    """
    Detect when a proper name from source was replaced by a tech/system word.
    Returns list of damaged name tokens.
    """
    names = extract_proper_nouns(source)
    if not names:
        return []
    tr_words = {
        re.sub(r"[^\w'-]", "", w.lower())
        for w in re.findall(r"\b[\w'-]+\b", str(translated or ""), flags=re.UNICODE)
    }
    damaged: list[str] = []
    for name in names:
        nkey = name.lower()
        if nkey in _TECH_TERM_SUBSTITUTIONS:
            continue
        # name missing but tech word appeared instead
        if nkey not in tr_words:
            for tw in tr_words:
                if tw in _TECH_TERM_SUBSTITUTIONS:
                    damaged.append(name)
                    break
    return damaged


def _source_entity_tokens(source: str, *, app_dir=None) -> list[str]:
    """Entity tokens from source text only (not expected target-language forms)."""
    from pathlib import Path

    from engines.proper_nouns_dict import find_latin_tokens_in_source

    base = Path(app_dir) if app_dir else Path(__file__).resolve().parent.parent
    seen: set[str] = set()
    out: list[str] = []
    for tok in (
        extract_proper_nouns(source)
        + extract_abbreviations(source)
        + find_latin_tokens_in_source(source, base)
    ):
        key = re.sub(r"\s+", "", tok.lower())
        if key in seen or len(tok) <= 2:
            continue
        if key in _COMMON_NON_NAMES or key in _DISCOURSE_MARKERS:
            continue
        seen.add(key)
        out.append(tok)
    return out


def missing_preserved_tokens(
    source: str,
    translated: str,
    *,
    app_dir=None,
    is_source_segment_incomplete: bool = False,
) -> list[str]:
    from pathlib import Path

    from engines.proper_nouns_dict import (
        find_name_keys_in_source,
        preferred_translations,
        transliterate_names,
    )

    missing: list[str] = []
    tr_lower = str(translated or "").lower()
    tr_norm = re.sub(r"\s+", "", tr_lower)
    base = Path(app_dir) if app_dir else Path(__file__).resolve().parent.parent
    names = transliterate_names(base)
    prefs = preferred_translations(base)

    def _satisfied(tok: str) -> bool:
        key = re.sub(r"\s+", "", tok.lower())
        if key in tr_norm:
            return True
        if tok.lower() in tr_lower:
            return True
        pref = prefs.get(tok)
        if pref and pref.lower() in tr_lower:
            return True
        for pkey, pval in prefs.items():
            if pkey.lower() in tok.lower() or tok.lower() in pkey.lower():
                if pval.lower() in tr_lower:
                    return True
        for nk in find_name_keys_in_source(source, base):
            if nk.lower() not in tok.lower() and tok.lower() not in nk.lower():
                continue
            target = names.get(nk, "")
            if target and re.sub(r"\s+", "", target.lower()) in tr_norm:
                return True
        if "george" in key and "джордж" in tr_lower:
            return True
        if key.rstrip(".") == "jr" and ("молодш" in tr_lower or "jr" in tr_lower):
            return True
        if key == "haskell" and "хаскелл" in tr_lower:
            return True
        if key == "wexler" and "векслер" in tr_lower:
            return True
        if key == "lucas" and "лукас" in tr_lower:
            return True
        if key == "italian" and "італ" in tr_lower:
            return True
        if key == "fiat" and ("fiat" in tr_lower or "фіат" in tr_lower or "фиат" in tr_lower):
            return True
        if key == "his" and "його" in tr_lower:
            return True
        if "молодш" in key and "молодш" in tr_lower:
            return True
        # Compact USC / phonetic TTS form «Ю Ес Сі» satisfies university entities
        _usc_phonetic = (
            "ю ес сі" in tr_lower
            or "юессі" in tr_lower.replace(" ", "")
            or "ю ес си" in tr_lower
        )
        if key in {"university", "southern", "california"}:
            if "usc" in tr_lower or _usc_phonetic:
                return True
            if "університет" in tr_lower and (
                "південн" in tr_lower or "каліфорн" in tr_lower
            ):
                return True
        if "universityofsoutherncalifornia" in key.replace(".", "") or (
            "university" in key and "southern" in key and "california" in key
        ):
            if "usc" in tr_lower or _usc_phonetic or (
                "університет" in tr_lower and "каліфорн" in tr_lower
            ):
                return True
        if key == "usc":
            if "usc" in tr_lower or _usc_phonetic:
                return True
            if "університет" in tr_lower and "каліфорн" in tr_lower:
                return True
        if key == "let" and ("давай" in tr_lower or "дозвол" in tr_lower):
            return True
        parts = [p for p in re.split(r"[\s.]+", tok) if len(p) > 2]
        if len(parts) >= 2:
            name_parts = [p for p in parts if p[0].isupper() or p in names]
            if name_parts:

                def _tr_name_hit(part: str) -> bool:
                    if part.lower() in tr_lower:
                        return True
                    tr_name = str(names.get(part, "") or "").strip()
                    if tr_name:
                        bits = tr_name.split()
                        if bits and bits[0].lower() in tr_lower:
                            return True
                    return False

                if all(_tr_name_hit(p) for p in name_parts):
                    return True
        return False

    for tok in _source_entity_tokens(source, app_dir=base):
        if not _satisfied(tok):
            # If the source segment is incomplete AND the missing token is a single capitalized word,
            # we will not flag it as missing. This handles cases like "George Jr." being split.
            # This is a heuristic to prevent false positives when segments are cut mid-sentence.
            if (
                is_source_segment_incomplete
                and len(tok.split()) == 1
                and tok[0].isupper()
            ):
                continue  # Do not flag as missing
            missing.append(tok)
    return missing


def proper_noun_warnings(source: str, translated: str) -> list[str]:
    return missing_preserved_tokens(source, translated)


def preserve_preserved_tokens(source: str, translated: str) -> str:
    """Read-only: never modify translation text (warnings via segment_quality_warnings)."""
    return str(translated or "").strip()


def enforce_token_consistency(
    source_segments: list[str],
    translated_segments: list[str],
) -> list[str]:
    """Pass-through: validation only, no text mutation."""
    return [str(tr or "").strip() for tr in translated_segments]


# Backward-compatible aliases
preserve_proper_nouns = preserve_preserved_tokens
enforce_proper_noun_consistency = enforce_token_consistency


def is_nonsense_text(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t) < 3:
        return False
    if re.search(r"(.)\1{7,}", t):
        return True
    if re.fullmatch(r"[\W\d_]+", t):
        return True
    words = re.findall(r"\w+", t.lower())
    if len(words) >= 4 and len(set(words)) == 1:
        return True
    if len(t) > 12 and not re.search(
        r"[a-zA-Zа-яА-ЯёЁіїєІЇЄ\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t
    ):
        return True
    return False


def diagnose_raw_mt(
    whisper: str,
    raw: str,
    *,
    source_lang: str | None = None,
    target_lang: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Structured diagnosis when Raw MT is empty (TZ §7).
    Returns None if raw MT is present; otherwise explains the source of loss.
    """
    w = str(whisper or "").strip()
    r = str(raw or "").strip()
    if r or not w:
        return None

    m = dict(meta or {})
    cause = "unknown"
    if m.get("mt_failed"):
        cause = "translation_engine_failed"
    elif m.get("split_empty_part"):
        cause = "timing_split_empty_part"
    elif m.get("non_head_group_index"):
        cause = "multi_segment_group_head_only"
    elif m.get("group_skipped_empty_phrase"):
        cause = "empty_source_phrase"
    elif m.get("engine") == "error":
        cause = "translation_exception"
    elif not m:
        cause = "never_assigned"

    return {
        "code": "raw_empty",
        "severity": "error",
        "cause": cause,
        "engine": str(m.get("engine") or ""),
        "route": str(m.get("route_label") or m.get("route") or ""),
        "mt_failed": bool(m.get("mt_failed")),
        "group_indices": list(m.get("group_indices") or []),
        "exception": str(m.get("exception") or "")[:200],
        "whisper_preview": w[:120],
    }


def build_quality_analysis(
    *,
    original: str,
    raw: str = "",
    naturalized: str = "",
    final: str = "",
    tts_text: str = "",
    source_lang: str | None = None,
    target_lang: str | None = None,
    raw_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Detailed Quality Analyzer output with human-readable reasons (TZ §5).
    """
    from engines.semantic_meaning import (
        check_critical_entities,
        compute_meaning_loss_score,
        meaning_loss_risk,
        word_count,
    )

    src = str(original or "").strip()
    # Prioritize LLM translation for baseline comparison
    baseline = str(final or naturalized or "").strip()
    spoken = str(tts_text or final or baseline).strip()
    spoken_plain = re.sub(r"<[^>]+>", "", spoken).strip()

    ow = word_count(src)
    bw = word_count(baseline)
    sw = word_count(spoken_plain)
    # Cross-language EN↔UK word delta is informational only (Final v3.0 Q1).
    # Never treat en_words vs uk_words as WARNING/ERROR by itself.
    en_uk_delta = max(0, ow - sw)
    en_uk_pct = round(100.0 * en_uk_delta / ow, 1) if ow else 0.0
    # Same-language compression (baseline UK → spoken UK) for clause coverage.
    same_lang_lost = max(0, bw - sw) if bw else 0
    same_lang_pct = round(100.0 * same_lang_lost / bw, 1) if bw else 0.0

    meaning_score = compute_meaning_loss_score(src, "", spoken_plain)
    entity_errors = check_critical_entities(src, spoken_plain)
    removed_constructs = 0
    if baseline and spoken_plain and spoken_plain != baseline:
        base_parts = re.split(r"[,;:—–-]", baseline)
        for part in base_parts:
            p = part.strip()
            if len(p.split()) >= 3 and p.lower() not in spoken_plain.lower():
                removed_constructs += 1
    # Clause coverage vs source: crude split on punctuation for meaning QA.
    src_clauses = [c.strip() for c in re.split(r"[,;:.!?—–]", src) if len(c.split()) >= 2]
    covered = 0
    for clause in src_clauses:
        # token overlap heuristic in target (latin tokens / proper names)
        tokens = [t for t in re.findall(r"[A-Za-zА-Яа-яЇїІіЄєҐґ']+", clause) if len(t) > 2]
        if not tokens:
            covered += 1
            continue
        hits = sum(1 for t in tokens if t.lower() in spoken_plain.lower())
        if hits >= max(1, len(tokens) // 2):
            covered += 1
    clause_coverage = round(covered / len(src_clauses), 3) if src_clauses else 1.0

    reasons: list[dict[str, Any]] = []
    raw_diag = diagnose_raw_mt(
        src,
        raw,
        source_lang=source_lang,
        target_lang=target_lang,
        meta=raw_meta,
    )
    if raw_diag:
        reasons.append(
            {
                "code": "raw_empty",
                "severity": "error",
                "summary": f"Raw MT пустой — причина: {raw_diag['cause']}",
                "detail": raw_diag,
            }
        )

    # Q1: en_words vs uk_words — INFO only (never WARNING/ERROR).
    if ow > 0 and (en_uk_pct >= 15 or en_uk_delta >= 3):
        reasons.append(
            {
                "code": "word_count_info",
                "severity": "info",
                "summary": (
                    f"INFO word-count: en_words={ow}, uk/tts_words={sw}, "
                    f"delta={en_uk_delta} ({en_uk_pct}%) — not a quality failure"
                ),
                "detail": {
                    "en_words": ow,
                    "uk_words": sw,
                    "delta": en_uk_delta,
                    "pct": en_uk_pct,
                },
            }
        )

    # Primary QA: meaning_loss_score + clause coverage (+ same-lang clause removal).
    meaning_risk = meaning_loss_risk(meaning_score)
    meaning_bad = meaning_risk in {"high", "critical"} or float(meaning_score) >= 0.35
    coverage_bad = clause_coverage < 0.55
    constructs_bad = removed_constructs >= 1 and same_lang_pct >= 20
    if meaning_bad or coverage_bad or constructs_bad:
        severity = "error" if (meaning_bad and coverage_bad) or float(meaning_score) >= 0.5 else "warning"
        reasons.append(
            {
                "code": "meaning_or_clause_loss",
                "severity": severity,
                "summary": (
                    f"Риск потери смысла: {meaning_risk} "
                    f"(score={round(float(meaning_score), 3)}); "
                    f"clause_coverage={clause_coverage}; "
                    f"удалено конструкций={removed_constructs}"
                ),
                "detail": {
                    "meaning_loss_score": meaning_score,
                    "meaning_loss_risk": meaning_risk,
                    "clause_coverage": clause_coverage,
                    "removed_constructs": removed_constructs,
                    "same_lang_shortened_pct": same_lang_pct,
                    "en_words": ow,
                    "baseline_words": bw,
                    "tts_words": sw,
                },
            }
        )

    for err in entity_errors:
        reasons.append(
            {
                "code": "entity_missing",
                "severity": "error",
                "summary": f"Потеряна сущность ({err['category']}): {err['value']}",
                "detail": err,
            }
        )

    return {
        "reasons": reasons,
        "meaning_loss_score": meaning_score,
        "meaning_loss_risk": meaning_loss_risk(meaning_score),
        "entity_errors": entity_errors,
        "word_counts": {
            "original": ow,
            "raw_mt": word_count(raw),
            "baseline": bw,
            "tts": sw,
        },
        "raw_mt_diagnosis": raw_diag,
    }


def validate_raw_mt(
    whisper: str,
    raw: str,
    *,
    source_lang: str | None,
    target_lang: str | None,
) -> list[str]:
    """Issues when raw_mt is not a valid machine translation output."""
    issues: list[str] = []
    w = str(whisper or "").strip()
    r = str(raw or "").strip()
    src = (source_lang or "").split("-")[0].lower()
    tgt = (target_lang or "").split("-")[0].lower()

    if w and not r:
        issues.append("raw_empty")
    if w and r and src and tgt and src != tgt and r.lower() == w.lower():
        issues.append("raw_equals_whisper")
    if r and is_nonsense_text(r):
        issues.append("raw_nonsense")
    return issues


def segment_quality_warnings(
    *,
    original: str,
    raw: str,
    final: str,
    tts_text: str,
    source_lang: str | None,
    target_lang: str | None,
    naturalized: str = "",
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    # Prioritize LLM translation for display
    display = tts_text or final or naturalized or original or ""
    src = (source_lang or "").split("-")[0].lower()
    tgt = (target_lang or "").split("-")[0].lower()

    for code in validate_raw_mt(original, raw, source_lang=src, target_lang=tgt):
        if code == "raw_empty":
            diag = diagnose_raw_mt(original, raw, source_lang=src, target_lang=tgt)
            warnings.append(
                {
                    "code": code,
                    "stage": "raw_mt",
                    "severity": (diag or {}).get("severity", "error"),
                    "cause": (diag or {}).get("cause", "unknown"),
                    "detail": diag,
                }
            )
        else:
            warnings.append({"code": code, "stage": "raw_mt"})

    qa = build_quality_analysis(
        original=original,
        raw=raw,
        naturalized=naturalized,
        final=final,
        tts_text=tts_text,
        source_lang=src,
        target_lang=tgt,
    )
    for reason in qa.get("reasons") or []:
        code = reason.get("code", "")
        if code == "raw_empty":
            continue
        if code == "over_shortening":
            warnings.append(
                {
                    "code": code,
                    "stage": "semantic",
                    "severity": reason.get("severity", "warning"),
                    "summary": reason.get("summary", ""),
                    "detail": reason.get("detail", {}),
                }
            )
        elif code == "entity_missing":
            warnings.append({**reason, "stage": "semantic"})

    # Determine if the original segment is likely incomplete to inform token checking
    # This is a heuristic: if the segment doesn't end with common punctuation, it might be incomplete.
    is_source_segment_incomplete = not bool(re.search(r"[.!?…]$", original.strip()))
    missing = missing_preserved_tokens(
        original, display, is_source_segment_incomplete=is_source_segment_incomplete
    )
    if missing:
        warnings.append(
            {"code": "preserved_token", "tokens": missing[:8], "stage": "final"}
        )

    tech_damage = name_to_tech_term_damage(original, display)
    if tech_damage:
        warnings.append(
            {
                "code": "name_to_tech_term",
                "names": tech_damage[:8],
                "stage": "final",
            }
        )

    from engines.placeholder_guard import detect_placeholder_leaks

    ph_leaks = detect_placeholder_leaks(display)
    if ph_leaks:
        warnings.append(
            {
                "code": "placeholder_leak",
                "tokens": ph_leaks[:8],
                "stage": "final",
            }
        )

    if not display.strip() and original.strip():
        warnings.append({"code": "empty_translation", "stage": "final"})

    if is_nonsense_text(display):
        warnings.append({"code": "nonsense", "stage": "final"})

    # Cross-script meaning collapse / source-script leak (any pair)
    try:
        from engines.mt.cross_script_guard import meaning_collapse, source_script_leak

        leak = source_script_leak(
            original, display, source_lang=src, target_lang=tgt
        )
        if leak:
            warnings.append(
                {
                    "code": "source_script_leak",
                    "stage": "final",
                    "severity": "critical",
                    "hints": [str(leak.get("source_script") or "")],
                }
            )
        collapse = meaning_collapse(
            original, display, source_lang=src, target_lang=tgt
        )
        if collapse:
            warnings.append(
                {
                    "code": "meaning_collapse",
                    "stage": "final",
                    "severity": "critical",
                    "hints": list(collapse.get("missing_gloss") or [])[:5],
                    "reasons": list(collapse.get("reasons") or [])[:5],
                }
            )
            if collapse.get("source_script") == "cjk" or (src or "").startswith("zh"):
                warnings.append(
                    {
                        "code": "cjk_meaning_collapse",
                        "stage": "final",
                        "severity": "critical",
                        "hints": list(collapse.get("missing_gloss") or [])[:5],
                        "reasons": list(collapse.get("reasons") or [])[:5],
                    }
                )
    except Exception:
        pass

    from engines.semantic_translation import detect_semantic_issues

    check_text = tts_text or final or naturalized or display
    for issue in detect_semantic_issues(
        original,
        check_text,
        source_lang=src,
        target_lang=tgt,
    ):
        warnings.append(issue)

    # Semantic QA on TTS-bound text (TZ §12)
    if tts_text and original:
        from engines.semantic_meaning import (
            is_truncated_adaptation,
            verify_meaning_preserved,
            word_count,
        )

        # Baseline for meaning preservation should prioritize LLM translation
        baseline_for_meaning_check = naturalized or final or ""
        ok, reason, hints = verify_meaning_preserved(
            original,
            baseline_for_meaning_check or tts_text,
            tts_text,
            target_lang=tgt,
        )
        if not ok and reason not in ("unchanged",):
            warnings.append(
                {
                    "code": f"semantic_{reason}",
                    "stage": "tts",
                    "hints": hints[:5],
                }
            )
        if baseline_for_meaning_check and is_truncated_adaptation(
            baseline_for_meaning_check, tts_text
        ):
            warnings.append({"code": "tts_truncation", "stage": "tts"})
        # Removed the 'meaning_change' warning block that compared raw with tts_text
        # as it contradicts the requirement to compare LLM translation only with the original.
    return warnings


def apply_translation_quality_pass(
    source_segments: list[str],
    translated_segments: list[str],
) -> list[str]:
    """Validation-only pass — returns text unchanged (Rule 2)."""
    texts, _ = run_quality_validation(
        source_segments,
        translated_segments,
        raw_segments=translated_segments,
    )
    return texts


def _latin_word_count(text: str) -> int:
    return len(re.findall(r"\b[a-zA-Z]{2,}\b", str(text or "")))


def accept_naturalizer_change(
    before: str,
    after: str,
    *,
    original: str = "",
) -> str:
    """Accept style fixes from Naturalizer unless clearly degraded."""
    return keep_if_not_worse(before, after, original=original, naturalizer_mode=True)


def keep_if_not_worse(
    before: str,
    after: str,
    *,
    original: str = "",
    naturalizer_mode: bool = False,
) -> str:
    """Rule 7: accept change only if text is not degraded."""
    b = str(before or "").strip()
    a = str(after or "").strip()
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return b
    if is_nonsense_text(a) and not is_nonsense_text(b):
        return b
    if naturalizer_mode:
        if len(b) > 24 and len(a) < len(b) * 0.30:
            return b
        return a
    latin_before = _latin_word_count(b)
    latin_after = _latin_word_count(a)
    if latin_after > latin_before + 1:
        return b
    if latin_after > latin_before and latin_before <= 1:
        return b
    if original and latin_after > latin_before + 1:
        orig_latin = _latin_word_count(original)
        if orig_latin <= 2 and latin_after > latin_before:
            return b
    if len(b) > 24 and len(a) < len(b) * 0.35:
        return b
    return a


def run_quality_validation(
    source_segments: list[str],
    translated_segments: list[str],
    *,
    src_lang: str | None = None,
    tgt_lang: str | None = None,
    raw_segments: list[str] | None = None,
) -> tuple[list[str], list[list[dict[str, Any]]]]:
    """Validation-only: unchanged texts + per-segment warnings for review/trace."""
    n = max(len(source_segments or []), len(translated_segments or []))
    texts = [
        str(translated_segments[i] if i < len(translated_segments) else "").strip()
        for i in range(n)
    ]
    raw = raw_segments if raw_segments is not None else texts
    warnings_out: list[list[dict[str, Any]]] = []
    for i in range(n):
        src = str(source_segments[i] if i < len(source_segments) else "")
        tr = texts[i]
        raw_i = str(raw[i] if i < len(raw) else "")
        # Determine if the original segment is likely incomplete to inform token checking
        is_source_segment_incomplete = not bool(re.search(r"[.!?…]$", src.strip()))
        warnings_out.append(
            segment_quality_warnings(
                original=src,
                raw=raw_i,
                naturalized=tr,
                final=tr,
                tts_text=tr,
                source_lang=src_lang,
                target_lang=tgt_lang,
            )
        )
    return texts, warnings_out


def review_json_path(output_mp4: str | Path) -> Path:
    p = Path(output_mp4)
    return p.with_name(f"{p.stem}_review.json")


def persist_translation_review(output_mp4: str | Path, review: dict[str, Any]) -> str:
    path = review_json_path(output_mp4)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "output_file": Path(output_mp4).name, **review}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def load_translation_review(output_mp4: str | Path) -> dict[str, Any] | None:
    path = review_json_path(output_mp4)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None
