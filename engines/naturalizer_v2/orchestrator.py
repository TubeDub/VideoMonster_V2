"""Naturalizer V2 orchestrator — editor-translator with retry."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from engines.naturalizer_v2.bad_patterns import has_bad_mt
from engines.naturalizer_v2.config import (
    MIXED_LANGUAGE_RETRY_THRESHOLD,
    QUALITY_RETRY_THRESHOLD,
)
from engines.naturalizer_v2.entity_tokens import restore_entities
from engines.naturalizer_v2.llm_rewrite import rewrite_segment_llm
from engines.naturalizer_v2.punctuation import clean_punctuation
from engines.naturalizer_v2.quality_validator import validate_naturalized_quality

logger = logging.getLogger("tubedub.engines.naturalizer_v2")

__all__ = ["polish_segment_v2"]


def polish_segment_v2(
    raw_mt: str,
    *,
    original: str = "",
    tgt_lang: str = "uk",
    src_lang: str | None = None,
    prev_context: str | None = None,
    app_dir=None,
    use_llm: bool = True,
    entity_token_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    V2 naturalization: rule pass → quality check → LLM rewrite → retry if needed.
    Returns dict compatible with NaturalizerResult extension.
    """
    from engines.translation_naturalizer import NaturalizerResult, _polish_v1_rules
    from engines.translation_quality import accept_naturalizer_change

    raw = str(raw_mt or "").strip()
    if not raw:
        return _result("", ["no_changes"], meta_only=True)

    base_dir = Path(app_dir) if app_dir else Path(__file__).resolve().parent.parent.parent
    reasons: list[str] = []
    warnings: list[str] = []
    retried = False
    retry_reason = ""
    restored: list[str] = []
    fix_count = 0

    # Phase 1 — rule-based pass (V1)
    v1: NaturalizerResult = _polish_v1_rules(
        raw,
        original=original,
        tgt_lang=tgt_lang,
        src_lang=src_lang,
        prev_context=prev_context,
        app_dir=base_dir,
        use_llm=False,
    )
    current = v1.text
    reasons.extend(v1.reasons)
    if v1.reasons != ["no_changes"]:
        fix_count += len(v1.reasons)

    # Phase 2 — punctuation
    punct = clean_punctuation(current)
    if punct != current:
        current = punct
        reasons.append("fixed_punctuation")
        fix_count += 1

    # Phase 3 — entity restore from tokens
    if entity_token_map:
        current, restored = restore_entities(
            current,
            entity_token_map,
            original=original,
            tgt_lang=tgt_lang,
            app_dir=base_dir,
        )
        if restored:
            reasons.append("restored_entities")
            fix_count += 1

    # Phase 4 — quality validation
    def _validate(text: str):
        return validate_naturalized_quality(
            original=original,
            raw_mt=raw,
            text=text,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            threshold=QUALITY_RETRY_THRESHOLD,
            mixed_threshold=MIXED_LANGUAGE_RETRY_THRESHOLD,
        )

    report = _validate(current)
    mixed_pct = report.mixed_language_pct

    def _needs_rewrite(q) -> bool:
        return q.needs_retry or has_bad_mt(current)

    # Phase 5 — LLM full rewrite when quality bad
    if use_llm and _needs_rewrite(report):
        from engines.proper_nouns_dict import extra_preserved_tokens

        preserved = extra_preserved_tokens(original, app_dir=base_dir) if original else []
        llm_out = rewrite_segment_llm(
            current,
            original=original,
            raw_mt=raw,
            tgt_lang=tgt_lang,
            src_lang=src_lang,
            prev_context=prev_context,
            problems=report.problems,
            preserved_entities=preserved,
        )
        if llm_out:
            accepted = accept_naturalizer_change(raw, llm_out, original=original)
            if accepted != raw:
                current = clean_punctuation(accepted)
                reasons.append("llm_full_rewrite")
                fix_count += 1
                retry_reason = report.retry_reason or "quality"
                report = _validate(current)
                mixed_pct = report.mixed_language_pct

    # Phase 6 — retry once if still bad (mixed > 3% or low score)
    if use_llm and report.needs_retry and not retried:
        retried = True
        retry_reason = report.retry_reason or "retry"
        from engines.proper_nouns_dict import extra_preserved_tokens

        llm_out2 = rewrite_segment_llm(
            current,
            original=original,
            raw_mt=raw,
            tgt_lang=tgt_lang,
            src_lang=src_lang,
            prev_context=prev_context,
            problems=report.problems + ["retry_pass"],
            preserved_entities=extra_preserved_tokens(original, app_dir=base_dir) if original else [],
            force=True,
        )
        if llm_out2:
            accepted2 = accept_naturalizer_change(raw, llm_out2, original=original)
            if accepted2 != raw:
                current = clean_punctuation(accepted2)
                reasons.append("retry_naturalization")
                fix_count += 1
                report = _validate(current)
                mixed_pct = report.mixed_language_pct

    if report.needs_retry:
        warnings.append(f"quality_warning:{report.retry_reason}")
        if mixed_pct > MIXED_LANGUAGE_RETRY_THRESHOLD:
            warnings.append(f"mixed_language_still:{mixed_pct}%")

    # Final entity polish
    if original.strip():
        from engines.proper_nouns_dict import apply_proper_noun_polish

        final_polish = apply_proper_noun_polish(
            original, current, app_dir=base_dir, tgt_lang=tgt_lang
        )
        if final_polish != current:
            current = final_polish
            if "fixed_named_entities" not in reasons:
                reasons.append("fixed_named_entities")
                fix_count += 1
        if (tgt_lang or "uk").split("-")[0].lower() == "ru":
            from engines.translation_naturalizer import fix_ru_jr_suffix

            ru_polish = fix_ru_jr_suffix(current)
            if ru_polish != current:
                current = ru_polish
                if "fixed_named_entities" not in reasons:
                    reasons.append("fixed_named_entities")
                    fix_count += 1
        if (tgt_lang or "uk").split("-")[0].lower() == "uk":
            from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

            uk_polish = apply_uk_dub_name_polish(current, original=original)
            if uk_polish != current:
                current = uk_polish
                if "fixed_named_entities" not in reasons:
                    reasons.append("fixed_named_entities")
                    fix_count += 1

    if current == raw and not reasons:
        reasons = ["no_changes"]

    return {
        "text": current,
        "reasons": sorted(set(reasons)),
        "mixed_language_pct": mixed_pct,
        "retry_reason": retry_reason,
        "problems": report.problems,
        "fix_count": fix_count,
        "quality_score": report.score,
        "restored_entities": restored,
        "warnings": warnings,
        "retried": retried,
        "needs_retry": report.needs_retry,
    }


def _result(text: str, reasons: list[str], *, meta_only: bool = False) -> dict[str, Any]:
    return {
        "text": text,
        "reasons": reasons,
        "mixed_language_pct": 0.0,
        "retry_reason": "",
        "problems": [],
        "fix_count": 0,
        "quality_score": 100.0 if meta_only else 0.0,
        "restored_entities": [],
        "warnings": [],
        "retried": False,
        "needs_retry": False,
    }
