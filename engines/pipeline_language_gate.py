"""Segment language validation and pipeline text trace logging."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.pipeline_language_gate")

_LATIN_WORD = re.compile(r"\b[A-Za-z]{2,}\b")
_CYRILLIC = re.compile(r"[а-яА-ЯёЁіїєІЇЄґҐ]")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af]")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]{3,}")
_ARABIC = re.compile(r"[\u0600-\u06ff]")

# Incidental 1–2 hanzi that may remain after transliteration of drama names
# (e.g. surname 李 / 王). Longer runs or denser residue stay blocked.
_CJK_NAME_WHITELIST = frozenset(
    {
        "李",
        "王",
        "张",
        "張",
        "刘",
        "劉",
        "陈",
        "陳",
        "杨",
        "楊",
        "赵",
        "趙",
        "黄",
        "黃",
        "周",
        "吴",
        "吳",
        "徐",
        "孙",
        "孫",
        "马",
        "馬",
        "朱",
        "胡",
        "郭",
        "何",
        "林",
        "罗",
        "羅",
        "高",
        "梁",
        "郑",
        "鄭",
        "谢",
        "謝",
        "宋",
        "唐",
        "许",
        "許",
        "邓",
        "鄧",
        "冯",
        "馮",
        "韩",
        "韓",
        "曹",
        "陆",
        "陸",
        "蒋",
        "蔣",
        "沈",
        "姚",
        "卢",
        "盧",
        "姜",
        "崔",
        "钟",
        "鍾",
        "谭",
        "譚",
        "曲",
        "妃",
    }
)


def _cjk_residue_is_incidental_name(text: str, *, cjk_n: int, cyr: int) -> bool:
    """True when 1–2 hanzi look like a name residue inside a real Cyrillic line."""
    if cjk_n <= 0 or cjk_n > 2:
        return False
    if cyr < 10:
        return False
    # A 3+ CJK run is a phrase leak, not a name token.
    if _CJK_RUN.search(text):
        return False
    tokens = _CJK.findall(text)
    if not tokens:
        return False
    # Allow if every residual char is a known surname/name glyph, OR the whole
    # residue is a single short token of length ≤2 (unlisted given-name chars).
    if all(ch in _CJK_NAME_WHITELIST for ch in tokens):
        return True
    # Compact residue: only one contiguous 1–2 char island in the line.
    islands = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]{1,2}", text)
    return len(islands) == 1 and len(islands[0]) <= 2 and cjk_n <= 2


def _base_lang(lang: str) -> str:
    return str(lang or "").strip().lower().split("-")[0]


def detect_segment_language(text: str, *, target_lang: str = "") -> str:
    """Full-text language label (entity-masked confidence when available)."""
    t = str(text or "").strip()
    if not t:
        return "empty"
    # Unified probabilistic detector over the entire string (never head-only).
    try:
        from engines.language_validation.entities import mask_entities
        from engines.language_validation.confidence import score_language_confidence

        masked, _ents = mask_entities(t)
        scored = score_language_confidence(
            masked, target_lang=target_lang, masked=True
        )
        det = str(scored.get("detected") or "")
        if det and det not in ("empty", "other", "unknown"):
            return det
    except Exception:
        pass
    try:
        from engines.mt.cross_script_guard import dominant_script

        dom = dominant_script(t, min_chars=6)
        if dom == "cjk":
            return "zh"
        if dom == "arabic":
            return "ar"
        if dom == "hebrew":
            return "he"
        if dom == "thai":
            return "th"
        if dom == "devanagari":
            return "hi"
        if dom == "cyrillic":
            base = _base_lang(target_lang)
            return base if base in ("uk", "ru", "be", "bg") else "ru"
        if dom == "latin":
            return "en"
    except Exception:
        pass

    cjk = len(_CJK.findall(t))
    cyr = len(_CYRILLIC.findall(t))
    lat_words = _LATIN_WORD.findall(t)
    lat_chars = sum(len(w) for w in lat_words)
    if cjk >= 8 and cjk >= max(cyr, lat_chars):
        return "zh"
    total = cyr + lat_chars
    if total == 0:
        return "unknown" if cjk == 0 else "zh"
    lat_ratio = lat_chars / total
    if lat_ratio > 0.45:
        return "en"
    base = _base_lang(target_lang)
    if base in ("uk", "ru", "be"):
        return base if cyr > 0 else "unknown"
    return base or "unknown"


def english_leak_tokens(text: str, original: str, target_lang: str) -> list[str]:
    from engines.language_intelligence import rules as R

    return R.detect_english_leak(text, original, target_lang)


def is_critical_language_mismatch(
    text: str,
    *,
    target_lang: str,
    original: str = "",
    source_lang: str = "",
) -> tuple[bool, str]:
    """True when final text is clearly not in the target language/script."""
    t = str(text or "").strip()
    if not t:
        return False, ""
    # Ignore brands / names / acronyms for script-ratio decisions.
    try:
        from engines.language_validation.entities import mask_entities

        masked, _ents = mask_entities(t)
        if masked.strip():
            t = masked
    except Exception:
        pass
    base = _base_lang(target_lang)

    try:
        from engines.mt.cross_script_guard import (
            dominant_script,
            expected_script,
            source_script_leak,
        )

        leak = source_script_leak(
            original or t,
            t,
            source_lang=source_lang or None,
            target_lang=target_lang,
        )
        # When original empty, still check dominant vs expected
        exp = expected_script(target_lang)
        dom = dominant_script(t, min_chars=8)
        if exp and dom and dom != exp:
            # Allow short latin brand tokens inside cyrillic
            if not (exp == "cyrillic" and dom == "latin" and len(_CYRILLIC.findall(t)) >= 12):
                if not (exp == "latin" and dom == "cyrillic" and len(_LATIN_WORD.findall(t)) >= 6):
                    return True, f"{dom}_in_{base or exp}_track"
        if leak and leak.get("reason") != "near_identity":
            # residual_source_script must always hard-fail (mixed UK+CJK tails)
            reason = str(leak.get("reason") or "")
            if (
                original
                or reason
                in (
                    "source_script_dominant",
                    "residual_source_script",
                )
            ):
                return True, f"source_script_leak_{leak.get('source_script')}"
    except Exception:
        pass

    # Cyrillic-target specifics (legacy paths)
    if base in ("uk", "ru", "be"):
        detected = detect_segment_language(t, target_lang=target_lang)
        cyr = len(_CYRILLIC.findall(t))
        lat = len(re.findall(r"[a-zA-Z]", t))
        cjk_n = len(_CJK.findall(t))
        # Residual CJK: block real leaks; allow 1–2 incidental name glyphs in
        # otherwise solid Cyrillic lines (avoids false-blocking «…Лу 李…»).
        if detected == "zh" or (
            cjk_n > 0
            and not _cjk_residue_is_incidental_name(t, cjk_n=cjk_n, cyr=cyr)
        ):
            return True, (
                f"cjk_in_{base}_track"
                if cjk_n >= 8 and cyr == 0
                else f"cjk_residue_in_{base}_track"
            )
        if detected == "ar" or (len(_ARABIC.findall(t)) >= 8 and cyr == 0):
            return True, f"arabic_in_{base}_track"
        if detected == "en":
            leaked = english_leak_tokens(t, original, target_lang)
            has_en_function = bool(
                re.search(
                    r"\b(that|was|from|the|and|but|had|have|been)\b", t, re.I
                )
            )
            # Brand/product Latin inside real Cyrillic lines is not critical
            # (e.g. «iPhone 15 Pro — нова модель від Apple»).
            if has_en_function or (leaked and cyr < 8):
                return True, f"english_in_{base}_track"
        # Tolerate brand Latin when the line has substantial Cyrillic content
        ratio_limit = 0.55 if cyr >= 10 else 0.35
        if cyr > 0 and lat > 0 and lat / max(cyr + lat, 1) > ratio_limit and lat >= cyr:
            return True, "latin_dominant_in_cyrillic_track"
        if cyr == 0 and lat > 8:
            return True, "no_cyrillic_in_target_track"

    # Latin-target: reject heavy Cyrillic / CJK / Arabic
    if base in ("en", "de", "fr", "es", "it", "pt", "pl", "nl"):
        if len(_CYRILLIC.findall(t)) >= 10 and len(_LATIN_WORD.findall(t)) < 3:
            return True, f"cyrillic_in_{base}_track"
        if len(_CJK.findall(t)) >= 8:
            return True, f"cjk_in_{base}_track"
        if len(_ARABIC.findall(t)) >= 8:
            return True, f"arabic_in_{base}_track"

    return False, ""


def log_segment_pipeline_trace(
    task_id: str,
    segments_data: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
    target_lang: str = "",
    audits: list[dict[str, Any]] | None = None,
) -> None:
    """Per-segment trace: original / translated / adapted / final / language / files."""
    audit_by = {int(a.get("index", -1)): a for a in (audits or [])}
    src_rows = list(source_segments or [])
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        audit = audit_by.get(idx, {})
        original = (
            src_rows[idx] if idx < len(src_rows) else str(seg.get("source_text") or "")
        )
        translated = str(
            audit.get("final_text")
            or audit.get("translation_text")
            or seg.get("translation_text")
            or seg.get("plain_text")
            or ""
        ).strip()
        adapted = str(
            seg.get("adapted_text")
            or audit.get("semantic_text")
            or seg.get("semantic_text")
            or ""
        ).strip()
        final = str(
            seg.get("text") or seg.get("plain_text") or seg.get("tts_text") or ""
        ).strip()
        lang = detect_segment_language(final, target_lang=target_lang)
        logger.info(
            "[SegmentTrace] task=%s idx=%d lang=%s target=%s "
            "original=%r translated=%r adapted=%r final=%r "
            "audio=%s fitted=%s",
            task_id or "?",
            idx,
            lang,
            target_lang,
            original[:120],
            translated[:120],
            adapted[:120],
            final[:120],
            seg.get("file") or seg.get("tts_file_path"),
            seg.get("fitted_file"),
        )


_PHRASE_LOOP_TEXT_KEYS = (
    "text",
    "plain_text",
    "translation_text",
    "final_text",
    "text_for_tts",
    "tts_text",
    "approved_text",
    "voice_input",
    "semantic_text",
    "adapted_text",
)


def heal_phrase_loops_in_segments(
    segments_data: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
    target_lang: str = "",
    source_lang: str = "",
) -> list[int]:
    """In-place deflate of Argos-style phrase loops. Returns healed indices.

    Prefer salvage (deflate + language checks) when source is available; fall
    back to raw deflate when that still clears ``has_phrase_loop``.
    """
    try:
        from engines.mt.cross_script_guard import deflate_phrase_loop, has_phrase_loop
    except Exception:
        return []

    healed: list[int] = []
    src_rows = list(source_segments or [])
    for idx, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None:
            continue
        primary = str(seg.get("text") or seg.get("plain_text") or "").strip()
        # Strip invented Stage-5 pacing pads before loop detection.
        try:
            from engines.text_slot_fit import strip_slot_pad_fillers

            cleaned_primary = strip_slot_pad_fillers(primary)
            if cleaned_primary != primary and cleaned_primary:
                for key in _PHRASE_LOOP_TEXT_KEYS:
                    cur = str(seg.get(key) or "")
                    if cur:
                        seg[key] = strip_slot_pad_fillers(cur)
                primary = cleaned_primary
                seg["slot_pad_stripped"] = True
        except Exception:
            pass
        # Detect loops a bit earlier (min_repeats=2) so Review/TTS catch
        # «у той момент у той момент» before synthesis.
        looped = bool(primary) and has_phrase_loop(primary, min_repeats=2)
        if not primary or not looped:
            dirty_keys = [
                k
                for k in _PHRASE_LOOP_TEXT_KEYS
                if has_phrase_loop(str(seg.get(k) or ""), min_repeats=2)
            ]
            if not dirty_keys:
                continue
            primary = str(seg.get(dirty_keys[0]) or "").strip()
            looped = True

        approved = str(seg.get("approved_text") or "").strip()
        final_t = str(seg.get("final_text") or "").strip()
        # Prefer a clean approved/final over deflating a looped primary.
        fixed, method = "", ""
        for cand, label in ((approved, "approved"), (final_t, "final")):
            if cand and not has_phrase_loop(cand, min_repeats=2):
                fixed, method = cand, f"prefer_clean_{label}"
                break

        original = src_rows[idx] if idx < len(src_rows) else ""
        if not fixed:
            fixed, method = salvage_collapsed_segment_text(
                text=primary,
                original=original,
                approved=approved,
                target_lang=target_lang or "uk",
                source_lang=source_lang or "",
            )
        if not fixed:
            raw = deflate_phrase_loop(primary)
            if raw and raw != primary and not has_phrase_loop(raw, min_repeats=2):
                fixed, method = raw, "raw_deflate_phrase_loop"
        if not fixed or fixed == primary:
            continue
        for key in _PHRASE_LOOP_TEXT_KEYS:
            cur = str(seg.get(key) or "")
            if not cur:
                continue
            if has_phrase_loop(cur, min_repeats=2) or key in (
                "text",
                "plain_text",
                "tts_text",
                "final_text",
                "text_for_tts",
            ):
                if has_phrase_loop(cur, min_repeats=2):
                    seg[key] = deflate_phrase_loop(cur) or fixed
                elif key in (
                    "text",
                    "plain_text",
                    "tts_text",
                    "text_for_tts",
                    "final_text",
                ):
                    seg[key] = fixed
        seg["text"] = fixed
        seg["plain_text"] = fixed
        seg["tts_text"] = fixed
        seg["final_text"] = fixed
        seg["text_for_tts"] = fixed
        seg["phrase_loop_healed"] = True
        seg["phrase_loop_heal_method"] = method
        # Unblock TTS when wipe was only due to a now-healed phrase loop.
        block_reason = str(seg.get("tts_blocked_reason") or "")
        reasons = set(seg.get("tps_reason_codes") or [])
        if seg.get("tts_blocked") or seg.get("skip_tts"):
            if block_reason in ("", "phrase_loop", "meaning_collapse") and not (
                reasons & {"source_script_leak", "cjk_meaning_collapse", "meaning_loss"}
            ):
                seg["tts_blocked"] = False
                seg["skip_tts"] = False
                seg["needs_manual_review"] = False
                if not str(seg.get("approved_text") or "").strip():
                    seg["approved_text"] = fixed
        healed.append(idx)
    return healed


def validate_segments_target_language(
    segments_data: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
    target_lang: str,
    source_lang: str = "",
    stage: str = "",
    hard_only: bool = False,
) -> list[dict[str, Any]]:
    """Return language/semantic issues via the unified Language Validation service.

    By default returns recoverable issues too (phrase_loop / meaning_collapse) so
    callers can run recovery. Pass ``hard_only=True`` after recovery to decide
    whether to stop the pipeline.

    Important: ``code=language_mismatch`` is used ONLY for true script/LID fails.
    Semantic problems use ``meaning_collapse`` / ``phrase_loop`` and must never be
    presented as Language Mismatch when expected==detected.
    """
    try:
        from engines.language_validation.service import validate_segments

        decisions = validate_segments(
            segments_data,
            source_segments=source_segments,
            target_lang=target_lang,
            source_lang=source_lang,
            stage=stage,
        )
        issues: list[dict[str, Any]] = []
        for d in decisions:
            if d.ok:
                continue
            if hard_only and not d.hard_fail:
                continue
            issues.append(d.to_issue())
        return issues
    except Exception as exc:
        logger.warning("unified language validation fallback: %s", exc)

    # Legacy fallback (should rarely run)
    issues = []
    src_rows = list(source_segments or [])
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        final = str(seg.get("text") or seg.get("plain_text") or "").strip()
        if not final:
            continue
        original = src_rows[idx] if idx < len(src_rows) else ""
        bad, code = is_critical_language_mismatch(
            final,
            target_lang=target_lang,
            original=original,
            source_lang=source_lang,
        )
        category = "language_mismatch" if bad else ""
        if not bad and original:
            try:
                from engines.mt.cross_script_guard import (
                    has_phrase_loop,
                    meaning_collapse,
                )

                if has_phrase_loop(final):
                    bad, code, category = True, "phrase_loop", "phrase_loop"
                else:
                    collapse = meaning_collapse(
                        original,
                        final,
                        source_lang=source_lang or None,
                        target_lang=target_lang,
                    )
                    if collapse:
                        bad, code, category = True, "meaning_collapse", "meaning_collapse"
            except Exception:
                pass
        if bad:
            detected = detect_segment_language(final, target_lang=target_lang)
            # Never label true language match as LANGUAGE_MISMATCH
            if (
                category == "language_mismatch"
                and detected == _base_lang(target_lang)
                and "cjk" not in code
                and "arabic" not in code
            ):
                category = "ambiguous"
                code = code or "ambiguous"
            issues.append(
                {
                    "index": idx,
                    "segment_id": seg.get("segment_id"),
                    "code": code,
                    "category": category,
                    "detected_lang": detected,
                    "target_lang": target_lang,
                    "final_preview": final[:200],
                    "hard_fail": category == "language_mismatch",
                }
            )
    if hard_only:
        issues = [i for i in issues if i.get("hard_fail")]
    return issues


def salvage_collapsed_segment_text(
    *,
    text: str,
    original: str,
    approved: str = "",
    target_lang: str,
    source_lang: str = "",
) -> tuple[str | None, str]:
    """Try to recover a voiceable target-language line without shipping nonsense.

    Order:
      1. Strip residual source-script from current / approved text
      2. LLM direct retranslate from source (CJK→uk/ru)
    Returns (salvaged_text_or_None, method).
    """
    tgt = str(target_lang or "").split("-")[0].lower()
    src = str(source_lang or "").split("-")[0].lower()
    current = str(text or "").strip()
    approved_s = str(approved or "").strip()
    try:
        from engines.mt.cross_script_guard import (
            deflate_phrase_loop,
            has_phrase_loop,
            meaning_collapse,
            strip_source_script_chars,
        )
    except Exception:
        return None, ""

    def _ok(cand: str) -> bool:
        if not cand or has_phrase_loop(cand, min_repeats=2):
            return False
        bad, _ = is_critical_language_mismatch(
            cand,
            target_lang=target_lang,
            original=original,
            source_lang=source_lang,
        )
        if bad:
            return False
        if original and meaning_collapse(
            original, cand, source_lang=source_lang or None, target_lang=target_lang
        ):
            return False
        return True

    # When current is looped, try clean approved first (avoid shipping deflated
    # garbage when Review already has a good Final).
    if has_phrase_loop(current, min_repeats=2) and approved_s:
        candidates = [
            ("approved", approved_s),
            ("current", current),
        ]
    else:
        candidates = [
            ("current", current),
            ("approved", approved_s),
        ]

    for label, cand in candidates:
        if not cand:
            continue
        if _ok(cand):
            return cand, f"{label}_ok"
        # Argos / closed-loop phrase loops: collapse repeats before giving up.
        if has_phrase_loop(cand, min_repeats=2):
            deflated = deflate_phrase_loop(cand)
            if deflated and deflated != cand and _ok(deflated):
                return deflated, f"{label}_deflate_phrase_loop"
            scrubbed_def = strip_source_script_chars(
                deflated or cand,
                source_lang=source_lang or None,
                source=original or None,
            )
            if scrubbed_def and _ok(scrubbed_def):
                return scrubbed_def, f"{label}_deflate_scrubbed"
        scrubbed = strip_source_script_chars(
            cand, source_lang=source_lang or None, source=original or None
        )
        if scrubbed and scrubbed != cand and _ok(scrubbed):
            return scrubbed, f"{label}_scrubbed"
        if scrubbed and has_phrase_loop(scrubbed):
            deflated = deflate_phrase_loop(scrubbed)
            if deflated and _ok(deflated):
                return deflated, f"{label}_scrubbed_deflate"

    # Offline curated gloss (zh drama) — works without LLM/network
    if original and src in ("zh", "ja", "ko", "yue", "zh-cn", "zh-tw") and tgt in (
        "uk",
        "ru",
        "be",
    ):
        try:
            from engines.mt.zh_drama_gloss import try_offline_gloss_rescue

            gloss = try_offline_gloss_rescue(
                original,
                text or approved,
                src_lang=src or "zh",
                tgt_lang=tgt or "uk",
            )
            if gloss and _ok(str(gloss.get("text") or "")):
                return str(gloss["text"]), f"offline_gloss:{gloss.get('method')}"
        except Exception:
            pass

    # LLM rescue for CJK→Cyrillic
    if original and src in ("zh", "ja", "ko", "yue", "zh-cn", "zh-tw") and tgt in (
        "uk",
        "ru",
        "be",
    ):
        try:
            from engines.mt.llm_retranslate import llm_direct_translate

            llm = llm_direct_translate(
                original, src_lang=src or "zh", tgt_lang=tgt or "uk"
            )
            if llm:
                scrubbed = strip_source_script_chars(
                    llm, source_lang=src or "zh", source=original
                ) or llm
                if _ok(scrubbed):
                    return scrubbed, "llm_direct"
        except Exception:
            pass
    return None, ""


def build_language_mismatch_report(
    *,
    index: int,
    segment: dict[str, Any],
    audit: dict[str, Any] | None,
    original: str,
    target_lang: str,
) -> dict[str, Any]:
    """Detailed RCA report for a LANGUAGE_MISMATCH segment (TZ §4, §8)."""
    a = audit or {}
    raw_stages = [
        ("original", original, "whisper_stt", "engines/whisper_*"),
        (
            "raw_mt",
            a.get("raw_translation") or a.get("raw_mt"),
            "translate_with_manager",
            "engines/translation_manager.py",
        ),
        (
            "naturalizer",
            a.get("naturalized_text"),
            "apply_naturalizer",
            "engines/naturalizer_v2/*",
        ),
        (
            "semantic_rewrite",
            a.get("semantic_text"),
            "apply_semantic_polish_lines",
            "engines/semantic_translation.py",
        ),
        (
            "final_translation",
            a.get("final_text")
            or segment.get("translation_text")
            or segment.get("plain_text")
            or segment.get("text"),
            "_translate_segments_body",
            "engines/translation_pipeline.py",
        ),
        (
            "tts_input",
            a.get("tts_text") or segment.get("tts_text"),
            "synthesize_segment",
            "engines/dubbing_engine/*",
        ),
    ]

    base = _base_lang(target_lang)
    chain: list[dict[str, Any]] = []
    first_non_target: dict[str, Any] | None = None
    prev_stage: dict[str, Any] | None = None
    for label, text, fn, src_file in raw_stages:
        t = str(text or "").strip()
        if not t:
            continue
        lang = detect_segment_language(t, target_lang=target_lang)
        bad, code = is_critical_language_mismatch(
            t, target_lang=target_lang, original=original
        )
        is_target = (base in ("uk", "ru", "be") and lang == base) or (
            not bad and label != "original"
        )
        entry = {
            "stage": label,
            "text_preview": t[:200],
            "lang": lang,
            "is_target_language": bool(is_target and not bad),
            "mismatch_code": code if bad else "",
            "source_function": fn,
            "source_file": src_file,
        }
        chain.append(entry)
        if label != "original" and bad and first_non_target is None:
            first_non_target = {
                "stage": label,
                "previous_stage": (prev_stage or {}).get("stage", "original"),
                "text_before": (prev_stage or {}).get("text_preview", original[:200]),
                "text_after": t[:200],
                "lang": lang,
                "mismatch_code": code,
                "source_function": fn,
                "source_file": src_file,
            }
        prev_stage = entry

    if first_non_target is None:
        diagnosis = (
            "Target language lost before any stage produced target-language text "
            "(machine translation likely returned the source unchanged)."
        )
    else:
        diagnosis = (
            f"Non-target text first appears at stage '{first_non_target['stage']}' "
            f"(after '{first_non_target['previous_stage']}'), produced by "
            f"{first_non_target['source_function']} ({first_non_target['source_file']}). "
            f"Reason code: {first_non_target['mismatch_code']}."
        )

    return {
        "index": index,
        "segment_id": segment.get("segment_id"),
        "target_lang": target_lang,
        "original_preview": str(original or "")[:200],
        "transformation_chain": chain,
        "first_non_target_stage": first_non_target,
        "diagnosis": diagnosis,
    }
