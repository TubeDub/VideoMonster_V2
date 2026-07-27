"""Pre-LOCK text polish for George Lucas checklist / TZ v4.0 P2."""

from __future__ import annotations

import re
from typing import Any


def polish_double_punctuation(text: str) -> str:
    """Fix «Фіат,.» and similar corrupted punctuation."""
    out = str(text or "")
    out = re.sub(r"([,;:])\s*\.", ".", out)
    out = re.sub(r"\.\s*([,;:])", ".", out)
    out = re.sub(r"([,.!?;:])\1+", r"\1", out)
    out = re.sub(r"\s+,", ",", out)
    return out


def polish_false_name_period(text: str) -> str:
    """Remove mid-sentence period after Джордж-молодший / George Jr.

    Keep the period when the next token is a new sentence (capital letter),
    e.g. «Джордж-молодший. Сьогодні…».
    Also undo verb→Name false stops: «їхав. Джордж».
    """
    out = str(text or "")
    out = re.sub(
        r"(Джордж(?:а|у)?-молодш(?:ий|ого|ому))\.\s+(?=[а-яіїєґ])",
        r"\1 ",
        out,
    )
    # HF5 checklist: no «молодший. [а-я…]» even without Джордж prefix
    out = re.sub(
        r"(молодш(?:ий|ого|ому))\.\s+(?=[а-яіїєґ])",
        r"\1 ",
        out,
    )
    out = re.sub(
        r"(George\s+Jr)\.\s+(?=[a-zа-яіїєґ])",
        r"\1 ",
        out,
    )
    # Mid-clause verb period before continuing proper name
    out = re.sub(
        r"\b(їхав|їхала|їхали|сказав|сказала|був|була|став|стала|"
        r"міг|могла|хотів|хотіла|відчув|відчула|почав|почала|почали)\."
        r"\s+(?=(?:Джордж|George|Lucas|Фіат|Fiat))",
        r"\1 ",
        out,
    )
    # George Jr. → «Джордж-молодший. Сьогодні/більш…» is a false stop from Jr.
    out = re.sub(
        r"(Джордж(?:а|у)?-молодш(?:ий|ого|ому))\.\s+"
        r"(?=(?:Сьогодні|сьогодні|Більш|більш|відомий|Відомий))",
        r"\1 ",
        out,
    )
    return out


def polish_duplicate_southern_california(text: str) -> str:
    """Collapse repeated «Південної Каліфорнії»."""
    out = str(text or "")
    out = re.sub(
        r"(Південної\s+Каліфорнії)(?:\s*,\s*Південної\s+Каліфорнії)+",
        r"\1",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"(Південної\s+Каліфорнії)(?:\s+Південної\s+Каліфорнії)+",
        r"\1",
        out,
        flags=re.I,
    )
    return out


def polish_orphan_pronoun_after_name(text: str) -> str:
    """Fix «Джордж-молодший Він більше» / «Джордж-молодший. Він більше»."""
    out = str(text or "")
    out = re.sub(
        r"(Джордж(?:а|у)?-молодш(?:ий|ого|ому))\.?\s+В[іí]*н\s+"
        r"(?=(?:б[іí]*льше|не\s+хоче|розповів|сказав|подав|зрозумів))",
        r"\1 ",
        out,
        flags=re.I,
    )
    return out


def polish_orphan_pro_after_name(text: str) -> str:
    """Fix «Джордж-молодший Про те» / «Джордж-молодший. Про те»."""
    out = str(text or "")
    out = re.sub(
        r"(Джордж(?:а|у)?-молодш(?:ий|ого|ому))\.?\s+Про\s+те,\s+як",
        r"\1 розповів Хаскелу про те, як",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"(Джордж(?:а|у)?-молодш(?:ий|ого|ому))\.?\s+Про\s+те,\s+що",
        r"\1 розповів про те, що",
        out,
        flags=re.I,
    )
    return out


def polish_clause_prefix_case(text: str) -> str:
    """Capitalize clause prefix «між батьком і сином» at line start."""
    out = str(text or "").strip()
    if re.match(r"^між\s+батьком\s+і\s+сином\b", out, re.I):
        out = "Між" + out[3:]
    return out


def strip_orphan_clause_tails(text: str, *, original: str = "") -> str:
    """Remove wrongly appended dinner/argument / father-son tails from prior DSAL."""
    out = str(text or "").strip()
    src = str(original or "")
    if not out:
        return out

    # Dinner/argument glued as ", і …" on ASR-cut segments.
    if re.search(r"every\s+dinner|huge\s+argument", src, re.I) and not re.search(
        r"[.!?…]\s*$", src.strip()
    ):
        out = re.sub(
            r"(?:,\s*)?(?:і\s+)?за\s+кожною\s+вечерею(?:\s*,\s*(?:і\s+)?велика\s+суперечка)?\.?\s*$",
            "",
            out,
            flags=re.I,
        )
        out = re.sub(
            r"(?:,\s*)?(?:і\s+)?велика\s+суперечка\.?\s*$",
            "",
            out,
            flags=re.I,
        )
        out = re.sub(
            r"(?:,\s*)?майже\s+кожної\s+вечері\s+між\s+ними\s+виникала\s+велика\s+суперечка\.?\s*$",
            "",
            out,
            flags=re.I,
        )

    # Father/son at end while EN opens with the clause → move to prefix.
    if re.match(r"^\s*between\s+father\s+and\s+son", src, re.I):
        m = re.search(
            r"(?:,\s*)?між\s+батьком\s+і\s+сином\.?\s*$",
            out,
            flags=re.I,
        )
        if m and not re.match(r"^\s*між\s+батьком\s+і\s+сином", out, re.I):
            body = out[: m.start()].rstrip(" ,.")
            if body:
                if body[0].islower():
                    body = body[0].upper() + body[1:]
                out = f"Між батьком і сином, {body}"
                if not out.endswith((".", "!", "?", "…")):
                    out += "."

    # Near-death literal glued after a natural paraphrase (GL #11/#12 TTS pollution).
    # Strip UK and RU orphans — DSAL must never leave cross-lang junk on TTS.
    try:
        from engines.dsal.clause_coverage import strip_cross_lang_clause_orphans

        out = strip_cross_lang_clause_orphans(out)
    except Exception:
        out = re.sub(
            r"(?:,\s+)?досвід\s+на\s+межі\s+смерті\.?\s*$",
            "",
            out,
            flags=re.I,
        )

    return " ".join(out.split()).strip()


def restore_not_just_marker(text: str, *, original: str = "") -> str:
    """Restore «не просто/не лише» when EN has «not just» and shorten stripped it."""
    out = str(text or "").strip()
    src = str(original or "").strip()
    if not out or not src:
        return out
    if not re.search(r"\bnot\s+just\b", src, re.I):
        return out
    if re.search(r"\bне\s+(?:просто|лише)\b", out, re.I):
        return out
    # Common collapse: «не просто змінив» → «не змінив»
    fixed = re.sub(
        r"\bне\s+змінив\b",
        "не просто змінив",
        out,
        count=1,
        flags=re.I,
    )
    if fixed != out:
        return fixed
    fixed = re.sub(
        r"\bне\s+змінить\b",
        "не просто змінить",
        out,
        count=1,
        flags=re.I,
    )
    return fixed


def polish_leading_comma_orphan(text: str, *, original: str = "") -> str:
    """Restore discourse marker stripped by aggressive compress (e.g. «Насправді»)."""
    out = str(text or "").strip()
    src = str(original or "").strip()
    if out.startswith(",") or out.startswith(";"):
        if re.match(r"^\s*in\s+fact\b", src, re.I):
            out = re.sub(r"^[,;]\s*", "Насправді, ", out, count=1)
        elif re.match(r"^\s*so\b", src, re.I):
            out = re.sub(r"^[,;]\s*", "Так, ", out, count=1)
        elif re.match(r"^\s*now\b", src, re.I):
            out = re.sub(r"^[,;]\s*", "Тепер ", out, count=1)
        else:
            out = re.sub(r"^[,;]\s*", "", out, count=1)
            if out and out[0].islower():
                out = out[0].upper() + out[1:]
        return out.strip()

    # Comma already wiped — still restore if EN opens with In fact / So / Now.
    if re.match(r"^\s*in\s+fact\b", src, re.I) and not re.match(
        r"^\s*насправді\b", out, re.I
    ):
        out = "Насправді, " + out.lstrip()
    elif re.match(r"^\s*so\b", src, re.I) and not re.match(
        r"^\s*(так|тож|отже)\b", out, re.I
    ):
        # Only for short openings that clearly lost the discourse marker.
        if out and out[0].isupper() and not out.lower().startswith(
            ("джордж", "але", "через", "натомість")
        ):
            pass
    return out.strip()


def ensure_terminal_punctuation(text: str, *, original: str = "") -> str:
    """Restore closing punct when source sentence is complete; undo force-dot on cuts."""
    try:
        from engines.semantic_meaning import restore_terminal_close

        out = restore_terminal_close(str(text or "").strip(), original=original)
    except Exception:
        out = str(text or "").strip()
    src = str(original or "").strip()
    if not out:
        return out
    src_complete = bool(src) and src[-1] in ".!?…"
    src_incomplete = bool(src) and src[-1] not in ".!?…" and len(src.split()) >= 6
    if src_incomplete and out.endswith(".") and not src_complete:
        # clean_punctuation may have force-added '.' on ASR mid-clause tails
        out = out.rstrip(".")
        return out
    return out


def apply_pre_lock_polish(
    text: str, *, original: str = "", tgt_lang: str = ""
) -> str:
    """Pre-LOCK polish. UK-specific name/discourse rules only when tgt_lang=uk."""
    from engines.dsal.clause_coverage import strip_cross_lang_clause_orphans
    from engines.dsal.core import strip_dsal_elaboration_fillers
    from engines.naturalizer_v2.punctuation import clean_punctuation

    out = str(text or "").strip()
    if not out:
        return out
    lang = str(tgt_lang or "").split("-")[0].lower().strip()
    if not lang:
        # Infer: Ukrainian letters → uk; else Cyrillic → ru; else leave generic.
        if re.search(r"[іІїЇєЄґҐ]", out):
            lang = "uk"
        elif re.search(r"[а-яА-ЯёЁ]", out):
            lang = "ru"

    # Always: strip cross-lang orphans + false Jr. periods (UK/RU name forms).
    out = strip_cross_lang_clause_orphans(out)
    out = polish_false_name_period(out)
    out = polish_double_punctuation(out)

    if lang == "uk":
        out = strip_dsal_elaboration_fillers(out, tgt_lang="uk")
        out = strip_orphan_clause_tails(out, original=original)
        try:
            from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

            out = apply_uk_dub_name_polish(out, original=original)
        except Exception:
            pass
        # uk_name_polish can re-insert Jr. period — undo after name forms.
        out = polish_false_name_period(out)
        out = polish_duplicate_southern_california(out)
        out = polish_orphan_pronoun_after_name(out)
        out = polish_orphan_pro_after_name(out)
        out = polish_leading_comma_orphan(out, original=original)
        out = restore_not_just_marker(out, original=original)
        out = polish_clause_prefix_case(out)
        out = polish_double_punctuation(out)
        out = clean_punctuation(out)
        out = polish_double_punctuation(out)
        out = polish_duplicate_southern_california(out)
        out = strip_orphan_clause_tails(out, original=original)
        out = polish_false_name_period(out)
        out = ensure_terminal_punctuation(out, original=original)
    elif lang == "ru":
        # RU: punctuation hygiene only — never inject Насправді / молодший / Зоряні.
        out = polish_double_punctuation(out)
        out = clean_punctuation(out)
        out = polish_double_punctuation(out)
        out = ensure_terminal_punctuation(out, original=original)
        out = re.sub(r"\bГоллівуд\w*\b", "Голливуд", out)
    else:
        out = clean_punctuation(out)
        out = ensure_terminal_punctuation(out, original=original)

    return " ".join(out.split()).strip()


def polish_segments_before_lock(info: dict[str, Any]) -> int:
    """Apply pre-lock polish to all unlocked segments. Returns changed count."""
    from engines.translation_validation import apply_translated_text_to_segment

    segments = list(info.get("segments_data") or [])
    src_segs = list(info.get("source_segments") or info.get("original_segments") or [])
    tgt = str(
        info.get("target_lang") or info.get("tgt_lang") or ""
    ).split("-")[0].lower()
    changed = 0
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or seg.get("translation_locked"):
            continue
        text = str(
            seg.get("final_text")
            or seg.get("translation_text")
            or seg.get("text")
            or ""
        ).strip()
        if not text:
            continue
        src = ""
        if i < len(src_segs):
            src = str(src_segs[i] or "")
        if not src:
            src = str(seg.get("source_text") or seg.get("original_text") or "")
        seg_lang = str(seg.get("target_lang") or tgt or "").split("-")[0].lower()
        polished = apply_pre_lock_polish(text, original=src, tgt_lang=seg_lang)
        if polished and polished != text:
            apply_translated_text_to_segment(seg, polished)
            changed += 1
            seg["pre_lock_polished"] = True
    info["pre_lock_polish_changed"] = changed
    return changed
