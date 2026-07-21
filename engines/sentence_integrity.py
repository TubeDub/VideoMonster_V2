"""Sentence & word integrity gate before TTS (AutoDub audit TЗ §3/§4/§6/§8).

This is the LAST line of defense before synthesis. Regardless of what any
upstream stage (MT split, rule shortening, LLM rewrite, timing adaptation,
repetition guard, …) did to a segment, no text may reach TTS if it is:

  • empty / whitespace-only / NULL / "none" / empty JSON ("{}", "[]");
  • a run of spaces only;
  • cut mid-word (a dangling word fragment / trailing hyphen);
  • an unfinished sentence (no terminal punctuation on a real sentence);
  • ending on a hanging connector (", і", "але", "that", "and", …).

When a candidate fails, we NEVER emit the broken text and NEVER emit empty
(TЗ §6: forbidden to delete words/endings/sentences). Instead we fall back to
the fullest COMPLETE version available (adapted → timing-before → translated →
raw MT), i.e. we prefer a slightly-too-long but *whole* sentence over a clipped
one. Every decision is reported so OpenDDF can show why a version was chosen.
"""

from __future__ import annotations

import re
from typing import Any

# Terminal punctuation that legitimately closes a spoken sentence.
_TERMINAL = ".!?…»\"')】」』"
_TERMINAL_RE = re.compile(r"[.!?…]['\"»)\]】」』]*\s*$")

# Trailing tokens that mean the sentence was cut off (a connector / preposition
# / article dangling at the very end). Small, high-precision multi-language set.
_DANGLING_TAIL = {
    # Ukrainian / Russian connectors & prepositions
    "і",
    "й",
    "та",
    "але",
    "а",
    "що",
    "щоб",
    "бо",
    "чи",
    "як",
    "коли",
    "де",
    "то",
    "не",
    "ні",
    "в",
    "у",
    "з",
    "зі",
    "із",
    "до",
    "на",
    "по",
    "за",
    "від",
    "для",
    "про",
    "під",
    "над",
    "при",
    "без",
    "через",
    "або",
    "и",
    "но",
    "или",
    "что",
    "чтобы",
    "потому",
    "когда",
    "где",
    "к",
    "со",
    "из",
    "о",
    "об",
    "для",
    "при",
    "над",
    "под",
    "без",
    "через",
    # English
    "and",
    "or",
    "but",
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "with",
    "that",
    "which",
    "as",
    "so",
    "if",
    "when",
    "while",
}

# NULL-ish / empty-JSON sentinels that must never be spoken.
_EMPTY_SENTINELS = {"", "none", "null", "nil", "nan", "{}", "[]", "()", '""', "''"}

# Minimum characters for a segment we treat as "real" (below this a missing
# terminal punctuation is not, by itself, treated as an incomplete sentence).
_MIN_SENTENCE_CHARS = 12
_MIN_SENTENCE_WORDS = 4


def normalize_spaces(text: str) -> str:
    """Collapse whitespace runs (TЗ §3: no space sequences reach TTS)."""
    return " ".join(str(text or "").split()).strip()


def _last_word(text: str) -> str:
    words = re.findall(r"[^\s]+", str(text or ""))
    return words[-1] if words else ""


def _strip_edge_punct(word: str) -> str:
    return re.sub(r"^[^\w]+|[^\w]+$", "", str(word or ""), flags=re.UNICODE)


def ends_mid_word(text: str) -> bool:
    """True when the text looks cut in the middle of a word.

    Signals: a trailing hyphen ("слово-"), or a final "word" that is a lone
    letter fragment with no sentence punctuation anywhere near it.
    """
    t = str(text or "").rstrip()
    if not t:
        return False
    if re.search(r"[\w][-‐]\s*$", t):  # trailing hyphen glued to a word
        return True
    return False


# CJK (Chinese/Japanese) + Hangul (Korean) code ranges. A weak multilingual
# local model (e.g. qwen2.5) can inject these into a non-CJK dub — that output
# is always corruption for a Ukrainian/European target and must be rejected.
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
_CJK_TARGET_LANGS = {"zh", "ja", "ko", "yue", "wuu", "zh-cn", "zh-tw"}


def contains_foreign_script(text: str, tgt_lang: str | None) -> bool:
    """True when `text` contains CJK/Hangul but the target language is not CJK.

    Used to reject weak-model hallucinations that leak foreign script into the
    dub (audit: qwen2.5:3b injected 良心 into a Ukrainian line). Never rejects
    when the target itself is a CJK language.
    """
    if not text:
        return False
    base = str(tgt_lang or "").split("-")[0].strip().lower()
    if not base or base in _CJK_TARGET_LANGS or (tgt_lang or "").strip().lower() in _CJK_TARGET_LANGS:
        return False
    return bool(_CJK_RE.search(text))


def validate_tts_text(
    text: str,
    *,
    min_chars: int = _MIN_SENTENCE_CHARS,
    tgt_lang: str | None = None,
    is_source_segment_incomplete: bool = False,
) -> tuple[bool, list[str]]:
    """Validate one final TTS string. Returns (ok, issues).

    Pure inspection — does not mutate. `issues` is a list of machine codes:
    empty | null_sentinel | empty_json | spaces_only | mid_word |
    dangling_connector | incomplete_sentence | foreign_script | too_short.

    When ``tgt_lang`` is a non-CJK language, any CJK/Hangul characters flag a
    ``foreign_script`` issue (weak-model corruption guard).
    """
    raw = str(text or "")
    stripped = raw.strip()
    issues: list[str] = []

    if not stripped:
        return False, ["empty"]

    if contains_foreign_script(stripped, tgt_lang):
        return False, ["foreign_script"]

    low = stripped.lower()
    if low in _EMPTY_SENTINELS:
        return False, ["null_sentinel"]
    if low in ("{}", "[]", "()"):
        return False, ["empty_json"]
    # A string that is only punctuation / symbols carries no speech.
    if not re.search(r"\w", stripped, flags=re.UNICODE):
        return False, ["empty"]

    if ends_mid_word(stripped):
        issues.append("mid_word")

    # Check for repeats (word repeated 3 times, or phrase repeated)
    if re.search(r"\b(\w{2,})\s+\1\s+\1\b", low):
        issues.append("repeats")

    # Check for corrupted punctuation
    if (
        re.search(r"[,;:]{2,}", stripped)
        or re.search(r"[.!?]{4,}", stripped)
        or re.search(r"[,;:.!?](?=[,;:.!?])[^.!?]*$", stripped)
    ):
        # A bit safer check for mixed punctuation or too many
        # Let's simplify: 2+ commas, 4+ dots/exclamations, or mixed like ,. or .,
        pass
    if re.search(r"[,;:]{2,}|[.!?]{4,}|[,;: ][.!?]|[.!?][,;:]", stripped):
        issues.append("corrupted_punctuation")

    last = _strip_edge_punct(_last_word(stripped)).lower()
    words = stripped.split()
    has_terminal = bool(_TERMINAL_RE.search(stripped))

    if last in _DANGLING_TAIL and not has_terminal:
        issues.append("dangling_connector")

    # Real sentence with no terminal punctuation → likely clipped.
    # Skip when source ASR cut is incomplete (avoids false Review warnings).
    if (
        not has_terminal
        and len(stripped) >= min_chars
        and len(words) >= _MIN_SENTENCE_WORDS
        and not is_source_segment_incomplete
    ):
        issues.append("incomplete_sentence")

    return (not issues), issues


def _completeness_rank(text: str) -> int:
    """Higher is a safer/more-complete candidate for fallback selection."""
    ok, _ = validate_tts_text(text)
    return 1 if ok else 0


def _fallback_usable(text: str, *, tgt_lang: str = "") -> bool:
    """True when a fallback string is valid TTS text in the target language."""
    ok, _ = validate_tts_text(text, tgt_lang=tgt_lang or None)
    if not ok:
        return False
    if not tgt_lang:
        return True
    try:
        from engines.pipeline_language_gate import is_critical_language_mismatch

        bad, _ = is_critical_language_mismatch(text, target_lang=tgt_lang)
        return not bad
    except Exception:
        return True


def enforce_tts_integrity(
    candidate: str,
    *,
    fallbacks: list[str] | None = None,
    source: str = "",
    tgt_lang: str = "",
) -> dict[str, Any]:
    """Guarantee a safe, complete TTS string.

    Returns a decision dict:
        text        — the text that MUST be sent to TTS (never empty/cut),
        changed     — whether we replaced the candidate,
        chosen      — where the final text came from
                      (candidate | fallback[i] | source | candidate_forced),
        issues      — issues detected on the candidate,
        rejected    — [{text, issues}] for each fallback we skipped,
        reason      — short human/machine reason.

    Selection order (TЗ §6/§8 — prefer a whole, slightly-long sentence over a
    clipped one; never emit empty; never truncate):
        1. candidate, if valid;
        2. first valid fallback (fullest complete translation);
        3. source (last resort, non-empty);
        4. candidate normalized (forced) — only if literally nothing else exists.
    """
    cand = normalize_spaces(candidate)
    cand_ok, cand_issues = validate_tts_text(cand, tgt_lang=tgt_lang or None)
    rejected: list[dict[str, Any]] = []

    if cand_ok and _fallback_usable(cand, tgt_lang=tgt_lang):
        return {
            "text": cand,
            "changed": cand != str(candidate or ""),
            "chosen": "candidate",
            "issues": [],
            "rejected": rejected,
            "reason": "candidate_valid",
        }

    # Candidate is broken — try the fuller fallbacks in priority order.
    for i, fb in enumerate(fallbacks or []):
        fb_norm = normalize_spaces(fb)
        if not fb_norm or fb_norm == cand:
            continue
        if not _fallback_usable(fb_norm, tgt_lang=tgt_lang):
            _, iss = validate_tts_text(fb_norm, tgt_lang=tgt_lang or None)
            rejected.append({"text": fb_norm[:200], "issues": iss})
            continue
        return {
            "text": fb_norm,
            "changed": True,
            "chosen": f"fallback[{i}]",
            "issues": cand_issues,
            "rejected": rejected,
            "reason": "reverted_to_complete",
        }

    src_norm = normalize_spaces(source)
    if src_norm and _fallback_usable(src_norm, tgt_lang=tgt_lang):
        return {
            "text": src_norm,
            "changed": True,
            "chosen": "source",
            "issues": cand_issues,
            "rejected": rejected,
            "reason": "reverted_to_source",
        }
    if src_norm and not _fallback_usable(src_norm, tgt_lang=tgt_lang):
        rejected.append(
            {
                "text": src_norm[:200],
                "issues": ["wrong_language_for_target"],
            }
        )

    # Nothing complete anywhere — keep the (normalized) candidate rather than
    # emit empty. This can only happen for genuinely tiny/odd inputs.
    return {
        "text": cand or normalize_spaces(str(candidate or "")),
        "changed": cand != str(candidate or ""),
        "chosen": "candidate_forced",
        "issues": cand_issues,
        "rejected": rejected,
        "reason": "no_complete_alternative",
    }


def enforce_pre_tts_integrity(
    segments: list[str],
    *,
    audits: list[dict[str, Any]] | None = None,
    source_segments: list[str] | None = None,
    target_lang: str = "",
) -> tuple[list[str], dict[str, Any]]:
    """Apply the integrity gate to every final segment before TTS.

    `audits` rows (per index) provide the fuller, complete fallbacks — we prefer
    the fullest complete translation over a clipped adaptation. Returns
    (fixed_segments, report) where report is OpenDDF-friendly.
    """
    audit_by_idx: dict[int, dict[str, Any]] = {}
    for a in audits or []:
        try:
            audit_by_idx[int(a.get("index", -1))] = a
        except (TypeError, ValueError):
            continue

    out: list[str] = []
    per_segment: list[dict[str, Any]] = []
    fixed_indices: list[int] = []

    for i, seg in enumerate(segments):
        row = audit_by_idx.get(i, {})
        # Fullest → shortest complete candidates, best fallback first.
        fallbacks = [
            str(row.get("naturalized_text") or ""),
            str(row.get("semantic_text") or ""),
            str(row.get("final_text") or ""),
            str(row.get("raw_translation") or ""),
        ]
        source = ""
        if source_segments and i < len(source_segments):
            source = str(source_segments[i] or "")
        source = source or str(row.get("original") or row.get("whisper_text") or "")

        decision = enforce_tts_integrity(
            seg, fallbacks=fallbacks, source=source, tgt_lang=target_lang
        )
        out.append(decision["text"])
        if decision["changed"] and decision["chosen"] != "candidate":
            fixed_indices.append(i)
        if decision["chosen"] != "candidate" or decision["issues"]:
            per_segment.append({"index": i, **decision})

    report = {
        "checked": len(segments),
        "fixed": len(fixed_indices),
        "fixed_indices": fixed_indices,
        "segments": per_segment,
    }
    return out, report
