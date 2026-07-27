"""Cross-script / cross-language MT guards — any source→target pair.

Detects:
  1. source_script_leak — translation still dominated by the source script
  2. source_script_residue — any non-trivial source-script leftovers in target
  3. meaning_collapse — fluent target text that is unrelated (service waffle /
     flower-delivery style hallucination, lost content cues)

Works for zh/ja/ko→uk/ru/en, ar→uk, en→uk (latin leak), uk→en, etc.
"""

from __future__ import annotations

import re
from typing import Any

# --- Script families ---------------------------------------------------------

_SCRIPTS: dict[str, re.Pattern[str]] = {
    "cjk": re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af]"),
    "cyrillic": re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]"),
    "latin": re.compile(r"[A-Za-z]"),
    "arabic": re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]"),
    "hebrew": re.compile(r"[\u0590-\u05ff]"),
    "thai": re.compile(r"[\u0e00-\u0e7f]"),
    "devanagari": re.compile(r"[\u0900-\u097f]"),
}

_LANG_SCRIPT: dict[str, str] = {
    "uk": "cyrillic",
    "ru": "cyrillic",
    "be": "cyrillic",
    "bg": "cyrillic",
    "sr": "cyrillic",
    "en": "latin",
    "de": "latin",
    "fr": "latin",
    "es": "latin",
    "it": "latin",
    "pt": "latin",
    "pl": "latin",
    "nl": "latin",
    "sv": "latin",
    "cs": "latin",
    "ro": "latin",
    "tr": "latin",
    "id": "latin",
    "vi": "latin",
    "zh": "cjk",
    "zh-cn": "cjk",
    "zh-tw": "cjk",
    "yue": "cjk",
    "ja": "cjk",
    "ko": "cjk",
    "ar": "arabic",
    "fa": "arabic",
    "ur": "arabic",
    "he": "hebrew",
    "th": "thai",
    "hi": "devanagari",
}

# Content cue families: source tokens → expected target gloss stems (multi-lang).
# First two families are critical for CJK drama dubbing (short segments still fail).
_CONTENT_FAMILIES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (
        ("怀孕", "身孕", "孕妇", "pregnant", "schwanger", "embarazad"),
        ("вагітн", "беремен", "pregnant", "schwanger", "embaraz"),
    ),
    (
        ("绑架", "绑匪", "绑费", "绑子", "kidnap", "abduct", "entführ"),
        ("викрал", "викрад", "похищ", "заложн", "kidnap", "abduct", "entführ", "secuestr"),
    ),
    (
        ("孩子", "小孩", "宝宝", "婴儿", "child", "baby", "niño", "niña"),
        ("дитин", "малюк", "ребен", "хлопчик", "дівчин", "child", "baby", "niñ"),
    ),
    (
        ("意外", "事故", "accident", "unfall"),
        ("випадк", "аварі", "несчастн", "accident", "unfall"),
    ),
    (
        ("八代", "单传", "子嗣", "有后", "陆家"),
        ("поколін", "спадкоєм", "нащадк", "родині лу", "родині", "спадкоємц", "лу "),
    ),
    (
        ("哥哥",),
        ("братик", "брат", "brother"),
    ),
    (
        ("妈", "妈妈", "母亲"),
        ("мамо", "мам", "матір", "mother", "mom"),
    ),
]
_CRITICAL_SRC_CUES = frozenset(
    {
        "怀孕",
        "身孕",
        "孕妇",
        "绑架",
        "绑匪",
        "绑费",
        "绑子",
    }
)
# 怀孕 → «народила» without pregnancy gloss = semantic flip (seen in zh→uk dumps)
_PREGNANCY_BIRTH_FLIP = re.compile(
    r"народил\w*|родил\w*|gave\s+birth|gave\s+birth",
    re.I,
)

# Offline-MT / Argos / Google-style fluent nonsense (any target).
_META_WAFFLE = re.compile(
    r"("
    # UK / RU
    r"має\s+на\s+увазі|имеет\s+в\s+виду|"
    r"використовується\s+для\s+того|используется\s+для\s+того|"
    r"просто\s+щось|просто\s+что-то|"
    r"зателефонувати\s+одержувачу|позвонить\s+получателю|"
    r"вручення\s+квітів|вручения\s+цветов|"
    r"збережемо\s+сюрприз|сохраним\s+сюрприз|"
    # EN
    r"\bhe\s+means\s+that\b|\bit\s+is\s+used\s+to\s+show\b|"
    r"\bcall\s+the\s+recipient\b|\bdelivery\s+of\s+flowers\b|"
    r"\bkeep\s+the\s+surprise\b|\bagree\s+on\s+a\s+convenient\s+time\b|"
    # DE
    r"anrufen\s+des?\s+empfängers|blumenlieferung|überraschung\s+bewahren|"
    # ES
    r"llamar\s+al\s+destinatario|entrega\s+de\s+flores|guardar\s+la\s+sorpresa"
    r")",
    re.I,
)


def is_meta_waffle(text: str) -> bool:
    """True for known offline-MT flower/delivery hallucinations (any language)."""
    return bool(_META_WAFFLE.search(str(text or "")))


def _norm_loop_token(word: str) -> str:
    """Strip edge punctuation so «момент,» matches «момент»."""
    return re.sub(r"^[,.;:!?«»\"']+|[,.;:!?«»\"']+$", "", str(word or ""))


def _phrase_loop_ns(word_count: int, *, min_n: int = 2) -> list[int]:
    """n-gram sizes to scan — must cover long STT echoes (10–20 tokens), not only ≤5."""
    max_n = min(24, max(min_n, word_count // 2))
    ns = list(range(max_n, min_n - 1, -1))
    return ns


def _token_jaccard_loop(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _has_sentence_echo_loop(text: str) -> bool:
    """True when a later sentence is a near-duplicate echo of earlier content."""
    parts = [
        p.strip()
        for p in re.split(r"(?<=[.!?…])\s+", str(text or "").strip())
        if p.strip()
    ]
    if len(parts) < 2:
        return False
    norms = [
        [_norm_loop_token(w) for w in p.split() if _norm_loop_token(w)] for p in parts
    ]
    for i in range(1, len(norms)):
        cur = norms[i]
        if len(cur) < 6:
            continue
        for prev in norms[:i]:
            if len(prev) < 6:
                continue
            # Suffix echo: "… as George Lucas. better known today as George Lucas…"
            if len(prev) >= len(cur) and prev[-len(cur) :] == cur:
                return True
            if _token_jaccard_loop(cur, prev) >= 0.82 and len(cur) >= int(
                0.55 * len(prev)
            ):
                return True
            # Cur mostly covered by prev (word-order shuffle / dropped head).
            cov = sum(1 for t in cur if t in set(prev)) / len(cur)
            if cov >= 0.85 and len(cur) >= 6:
                return True
    return False


def _deflate_sentence_echo(text: str) -> str:
    """Drop trailing sentences that echo earlier content (George Lucas / Star Wars)."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", raw) if p.strip()]
    if len(parts) < 2:
        return raw
    kept: list[str] = []
    kept_norms: list[list[str]] = []
    for p in parts:
        cur = [_norm_loop_token(w) for w in p.split() if _norm_loop_token(w)]
        if len(cur) >= 6 and kept_norms:
            echo = False
            for prev in kept_norms:
                if len(prev) >= len(cur) and prev[-len(cur) :] == cur:
                    echo = True
                    break
                if _token_jaccard_loop(cur, prev) >= 0.82 and len(cur) >= int(
                    0.55 * len(prev)
                ):
                    echo = True
                    break
                cov = sum(1 for t in cur if t in set(prev)) / len(cur)
                if cov >= 0.85:
                    echo = True
                    break
            if echo:
                continue
        kept.append(p)
        kept_norms.append(cur)
    return " ".join(kept).strip() or raw


def has_phrase_loop(text: str, *, min_repeats: int = 3) -> bool:
    """Detect Argos/STT phrase loops (consecutive n-grams and sentence echoes)."""
    raw_words = str(text or "").split()
    words = [_norm_loop_token(w) for w in raw_words]
    words = [w for w in words if w]
    if len(words) < min_repeats * 2:
        return False
    for n in _phrase_loop_ns(len(words)):
        if len(words) < n * min_repeats:
            continue
        limit = len(words) - n * min_repeats + 1
        for i in range(max(0, limit)):
            chunk = words[i : i + n]
            if not any(len(w) >= 2 for w in chunk):
                continue
            ok = True
            for r in range(1, min_repeats):
                if words[i + r * n : i + (r + 1) * n] != chunk:
                    ok = False
                    break
            if ok:
                return True
    # Non-adjacent long exact span (same STT echo with a short glue gap).
    if min_repeats <= 2:
        for n in _phrase_loop_ns(len(words), min_n=5):
            seen: dict[tuple[str, ...], int] = {}
            for i in range(0, len(words) - n + 1):
                chunk = tuple(words[i : i + n])
                if not any(len(w) >= 2 for w in chunk):
                    continue
                prev = seen.get(chunk)
                if prev is not None and i >= prev + n:
                    return True
                seen.setdefault(chunk, i)
    return _has_sentence_echo_loop(text)


def deflate_phrase_loop(text: str, *, min_repeats: int = 2) -> str:
    """Collapse consecutive n-gram phrase loops to a single occurrence.

    «у той момент,» × N → one copy. Also drops sentence-level echoes
    (George Lucas / Star Wars tail repeat). Safe for TTS / language-gate heal.
    """
    # Sentence echoes first — avoids n-gram surgery mangling near-duplicate tails.
    raw = _deflate_sentence_echo(str(text or "").strip())
    raw_words = raw.split()
    if not raw_words:
        return ""

    # Iterate: longest n-gram, most repeats, earliest position wins each pass.
    for _ in range(32):
        norms = [_norm_loop_token(w) for w in raw_words]
        best: tuple[int, int, int] | None = None  # start, n, repeats
        for n in _phrase_loop_ns(len(raw_words)):
            if len(raw_words) < n * min_repeats:
                continue
            limit = len(raw_words) - n * min_repeats + 1
            for i in range(max(0, limit)):
                chunk = norms[i : i + n]
                if not any(len(w) >= 2 for w in chunk):
                    continue
                if any(not w for w in chunk):
                    continue
                r = 1
                while (
                    i + (r + 1) * n <= len(raw_words)
                    and norms[i + r * n : i + (r + 1) * n] == chunk
                ):
                    r += 1
                if r < min_repeats:
                    continue
                cand = (i, n, r)
                if best is None:
                    best = cand
                    continue
                # Prefer longer phrases, then more repeats, then earlier offset.
                bi, bn, br = best
                if (n, r, -i) > (bn, br, -bi):
                    best = cand
        if best is None:
            # Non-adjacent exact span: drop glue between copies + later copy.
            removed = False
            for n in _phrase_loop_ns(len(raw_words), min_n=5):
                norms = [_norm_loop_token(w) for w in raw_words]
                seen: dict[tuple[str, ...], int] = {}
                for i in range(0, len(raw_words) - n + 1):
                    chunk = tuple(norms[i : i + n])
                    if not any(len(w) >= 2 for w in chunk):
                        continue
                    prev = seen.get(chunk)
                    if prev is not None and i >= prev + n:
                        # Keep first copy; remove (gap + second copy).
                        raw_words = raw_words[: prev + n] + raw_words[i + n :]
                        removed = True
                        break
                    seen.setdefault(chunk, i)
                if removed:
                    break
            if not removed:
                break
            continue
        i, n, r = best
        raw_words = raw_words[: i + n] + raw_words[i + r * n :]
    out = " ".join(raw_words).strip()
    echoed = _deflate_sentence_echo(out)
    if echoed and echoed != out:
        out = echoed
    return out


def strip_source_script_chars(
    text: str,
    *,
    source_lang: str | None = None,
    source: str | None = None,
) -> str:
    """Remove residual CJK/Arabic/etc. runs from a Cyrillic/Latin dub line."""
    src_script = expected_script(source_lang) or (
        dominant_script(source or "") if source else None
    )
    if src_script not in _SCRIPTS:
        # Default: strip CJK when unknown but CJK present
        src_script = "cjk" if script_counts(text).get("cjk", 0) else None
    if not src_script:
        return str(text or "").strip()
    pat = _SCRIPTS[src_script]
    cleaned = pat.sub(" ", str(text or ""))
    if src_script == "cjk":
        # Also drop CJK punctuation left beside scrubbed runs
        cleaned = re.sub(r"[，、。！？：；「」『』【】《》（）…．]+", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?;:…])", r"\1", cleaned)
    cleaned = re.sub(r"([,/\\|]){2,}", " ", cleaned)
    cleaned = cleaned.strip(" ,;:/\\|")
    # Drop trailing orphan short tokens left after a scrubbed CJK clause
    parts = cleaned.split()
    while parts:
        last = parts[-1].strip(".,!?;:…\"'«»")
        if not last:
            parts.pop()
            continue
        # Lone Cyrillic stub after scrub ("Той", "а") when previous ends a sentence
        if len(last) <= 3 and len(parts) >= 2 and re.search(
            r"[.!?…]$", parts[-2]
        ):
            parts.pop()
            continue
        break
    cleaned = " ".join(parts).strip()
    # Ensure we end on sentence punctuation when we trimmed a scrubbed tail
    if cleaned and not re.search(r"[.!?…]$", cleaned):
        # Prefer last full sentence
        m = list(re.finditer(r"[.!?…]", cleaned))
        if m and m[-1].end() >= max(12, int(len(cleaned) * 0.5)):
            cleaned = cleaned[: m[-1].end()].strip()
    return cleaned.strip()


def _base_lang(lang: str | None) -> str:
    return str(lang or "").strip().lower().split("-")[0]


def script_counts(text: str) -> dict[str, int]:
    t = str(text or "")
    return {name: len(pat.findall(t)) for name, pat in _SCRIPTS.items()}


def dominant_script(text: str, *, min_chars: int = 6) -> str | None:
    counts = script_counts(text)
    best_name = None
    best_n = 0
    for name, n in counts.items():
        if n > best_n:
            best_n = n
            best_name = name
    if best_n < min_chars:
        return None
    return best_name


def expected_script(lang: str | None) -> str | None:
    return _LANG_SCRIPT.get(_base_lang(lang))


def is_script_heavy(text: str, script: str, *, min_chars: int = 10) -> bool:
    n = script_counts(text).get(script, 0)
    if n < min_chars:
        return False
    total = max(1, sum(script_counts(text).values()))
    return n / total >= 0.35 or n >= 40


def source_script_leak(
    source: str,
    translated: str,
    *,
    source_lang: str | None = None,
    target_lang: str | None = None,
) -> dict[str, Any] | None:
    """Translation still looks like the source language/script."""
    src = str(source or "").strip()
    tgt = str(translated or "").strip()
    if not src or not tgt:
        return None

    tgt_script = expected_script(target_lang)
    src_script = expected_script(source_lang) or dominant_script(src)
    if not src_script:
        return None

    # Same-script pairs (en→de): use overlap / identity, not script family
    if tgt_script and src_script == tgt_script:
        # Near-copy of source into "translation"
        src_norm = re.sub(r"\s+", " ", src.lower())
        tgt_norm = re.sub(r"\s+", " ", tgt.lower())
        if len(src_norm) >= 24 and (
            src_norm == tgt_norm
            or (src_norm in tgt_norm and len(src_norm) / max(1, len(tgt_norm)) > 0.85)
            or (tgt_norm in src_norm and len(tgt_norm) / max(1, len(src_norm)) > 0.85)
        ):
            return {
                "code": "source_script_leak",
                "severity": "critical",
                "source_script": src_script,
                "target_script": tgt_script,
                "reason": "near_identity",
            }
        return None

    counts = script_counts(tgt)
    residual = counts.get(src_script, 0)
    # Residual CJK/Arabic/etc. in Cyrillic/Latin dub — even a short tail is a leak
    # (_tmp_3333 approved text ended with Chinese after mostly-UK retry).
    if (
        src_script in ("cjk", "arabic", "hebrew", "thai", "devanagari")
        and tgt_script in ("cyrillic", "latin")
        and residual >= 4
    ):
        return {
            "code": "source_script_leak",
            "severity": "critical",
            "source_script": src_script,
            "target_script": tgt_script,
            "reason": "residual_source_script",
            "counts": counts,
            "residual_chars": residual,
        }

    # Cross-script: translation dominated by source script, missing target script
    if not is_script_heavy(tgt, src_script, min_chars=8):
        return None
    tgt_n = counts.get(tgt_script or "", 0) if tgt_script else 0
    src_n = residual
    if tgt_script and tgt_n >= max(8, int(src_n * 0.35)):
        return None  # mixed but has enough target script
    return {
        "code": "source_script_leak",
        "severity": "critical",
        "source_script": src_script,
        "target_script": tgt_script,
        "reason": "source_script_dominant",
        "counts": counts,
    }


def meaning_collapse(
    source: str,
    translated: str,
    *,
    source_lang: str | None = None,
    target_lang: str | None = None,
) -> dict[str, Any] | None:
    """Fluent but unrelated MT (meta-waffle / lost content cues)."""
    src = str(source or "").strip()
    tgt = str(translated or "").strip()
    if not src or not tgt:
        return None

    # Source-script leak is a separate code — still treat as collapse sibling
    leak = source_script_leak(
        src, tgt, source_lang=source_lang, target_lang=target_lang
    )

    src_hits: list[str] = []
    missing_gloss: list[str] = []
    tgt_lwr = tgt.lower()
    src_lwr = src.lower()

    def _src_has_cue(cue: str) -> bool:
        if cue.isascii():
            # Whole-word for short Latin cues («kind» must not match «kind of»)
            if len(cue) <= 5:
                return bool(re.search(rf"\b{re.escape(cue.lower())}\b", src_lwr))
            return cue.lower() in src_lwr
        return cue in src

    for cues, glosses in _CONTENT_FAMILIES:
        if any(_src_has_cue(c) for c in cues):
            hit = next((c for c in cues if _src_has_cue(c)), cues[0])
            src_hits.append(hit)
            if not any(g in tgt_lwr for g in glosses):
                missing_gloss.append(cues[0])

    waffle = bool(_META_WAFFLE.search(tgt))
    src_len = max(
        sum(script_counts(src).values()),
        len(re.findall(r"\S", src)),
    )

    collapse = False
    reasons: list[str] = []
    covered = max(0, len(src_hits) - len(missing_gloss))
    critical_missing = [m for m in missing_gloss if m in _CRITICAL_SRC_CUES]
    if critical_missing:
        # Short drama lines («你怀孕了») must still fail when pregnancy/kidnap gloss absent
        collapse = True
        reasons.append("critical_cue_lost:" + ",".join(critical_missing[:4]))
    if missing_gloss and waffle:
        collapse = True
        reasons.append("lost_content:" + ",".join(missing_gloss[:4]))
        reasons.append("meta_waffle")
    elif missing_gloss and src_len >= 40 and not waffle and not critical_missing:
        # Long ASR/MT: fail if no cues landed OR majority of cue families dropped
        # (one lucky hit like «вагітні» must not greenlight a collapsed zh→uk dump).
        majority_lost = len(src_hits) >= 2 and len(missing_gloss) * 2 >= len(src_hits)
        if covered <= 0 or majority_lost:
            collapse = True
            reasons.append("lost_content:" + ",".join(missing_gloss[:4]))
            if majority_lost and covered > 0:
                reasons.append("partial_cue_collapse")
    # 怀孕 → «народила хлопчика» without pregnancy gloss (zh→uk dump flip)
    if any(c in src for c in ("怀孕", "身孕", "孕妇")) and _PREGNANCY_BIRTH_FLIP.search(
        tgt
    ):
        if not any(g in tgt_lwr for g in ("вагітн", "беремен", "pregnant")):
            collapse = True
            reasons.append("pregnancy_to_birth_flip")
    if has_phrase_loop(tgt, min_repeats=3):
        # Always diagnose the loop; only treat as collapse when it cannot be deflated.
        reasons.append("phrase_loop")
        try:
            deflated = deflate_phrase_loop(tgt)
        except Exception:
            deflated = ""
        if not deflated or has_phrase_loop(deflated, min_repeats=2):
            collapse = True
    if waffle and src_len >= 40:
        collapse = True
        if "meta_waffle_hallucination" not in reasons:
            reasons.append("meta_waffle_hallucination")

    # Cross-script + short relative coverage (char proxy for CJK sources)
    src_script = expected_script(source_lang) or dominant_script(src)
    tgt_script = expected_script(target_lang) or dominant_script(tgt)
    if (
        src_script
        and tgt_script
        and src_script != tgt_script
        and is_script_heavy(src, src_script, min_chars=30)
        and not leak
    ):
        tgt_letters = script_counts(tgt).get(tgt_script, 0)
        src_chars = script_counts(src).get(src_script, 0)
        if src_chars >= 40 and tgt_letters < max(12, int(src_chars * 0.12)):
            collapse = True
            reasons.append("cross_script_under_coverage")

    if leak and not collapse and waffle:
        collapse = True
        reasons.append("leak_plus_waffle")

    if not collapse:
        # Keep zh-specific path for callers that only check meaning_collapse_zh
        return None

    return {
        "code": "meaning_collapse",
        "severity": "critical",
        "reasons": reasons,
        "source_hits": src_hits[:8],
        "missing_gloss": missing_gloss[:6],
        "meta_waffle": waffle,
        "source_script": src_script,
        "target_script": tgt_script,
        "leak": leak,
    }


# --- Backward-compatible aliases (zh path) -----------------------------------

def cjk_char_count(text: str) -> int:
    return script_counts(text).get("cjk", 0)


def is_cjk_heavy(text: str, *, min_chars: int = 12) -> bool:
    return is_script_heavy(text, "cjk", min_chars=min_chars)


def meaning_collapse_zh_to_cyrillic(
    source: str,
    translated: str,
    *,
    source_lang: str | None = None,
    target_lang: str | None = None,
) -> dict[str, Any] | None:
    """Legacy API — maps to universal meaning_collapse + leak."""
    leak = source_script_leak(
        source, translated, source_lang=source_lang or "zh", target_lang=target_lang
    )
    hit = meaning_collapse(
        source,
        translated,
        source_lang=source_lang or "zh",
        target_lang=target_lang,
    )
    if hit:
        hit = {**hit, "code": "cjk_meaning_collapse"}
        return hit
    if leak and is_cjk_heavy(source, min_chars=12):
        # Cyrillic flower waffle without CJK cues still caught via waffle alone
        if _META_WAFFLE.search(str(translated or "")):
            return {
                "code": "cjk_meaning_collapse",
                "severity": "critical",
                "reasons": ["meta_waffle_hallucination"],
                "source_hits": [],
                "missing_gloss": [],
                "meta_waffle": True,
                "leak": leak,
            }
    # Flower text with CJK source always collapse via meaning_collapse when cues present
    if hit is None and is_cjk_heavy(source) and _META_WAFFLE.search(str(translated or "")):
        return {
            "code": "cjk_meaning_collapse",
            "severity": "critical",
            "reasons": ["meta_waffle_hallucination"],
            "cjk_chars": cjk_char_count(source),
            "source_hits": [],
            "missing_gloss": [],
            "meta_waffle": True,
        }
    return None
