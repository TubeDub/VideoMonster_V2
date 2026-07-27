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
    use_llm: bool = False,
    entity_token_map: dict[str, str] | None = None,
    slot_ms: int = 0,
    reserve_ms: int | None = None,
) -> dict[str, Any]:
    """
    V2 naturalization: rule pass → quality check → (optional) LLM rewrite → CATP.
    Default ``use_llm=False`` — engine-first (MT + rules). Heavy LLMs are gated
    by ``engines.llm_kill_switch``.
    """
    from engines.translation_naturalizer import NaturalizerResult, _polish_v1_rules
    from engines.translation_quality import accept_naturalizer_change

    try:
        from engines.llm_kill_switch import is_heavy_llm_disabled

        if is_heavy_llm_disabled():
            use_llm = False
    except Exception:
        use_llm = False

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
    catp_meta: dict[str, Any] = {}
    literary_candidate = ""

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
    # Snapshot before literary (Safe Polish baseline for CATP)
    safe_baseline = current

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

    lang = (tgt_lang or "uk").split("-")[0].lower()

    # Phase 4b — offline literary candidate (UK). Not committed until CATP.
    if lang == "uk":
        from engines.naturalizer_v2.literary_uk import apply_literary_uk

        lit, lit_codes = apply_literary_uk(current, original=original)
        if lit != current and lit_codes:
            accepted_lit = accept_naturalizer_change(raw, lit, original=original)
            if accepted_lit != current:
                literary_candidate = accepted_lit
                # Tentatively apply; CATP may roll back
                current = accepted_lit
                reasons.append("literary_uk")
                fix_count += 1
                report = _validate(current)
                mixed_pct = report.mixed_language_pct

    def _catp_mode_allows_extended() -> bool:
        try:
            from engines.naturalizer_v2.catp import compute_budget

            b = compute_budget(
                slot_ms=slot_ms,
                reserve_ms=reserve_ms,
                baseline_text=safe_baseline or current,
                lang=lang,
            )
            return b.mode == "extended"
        except Exception:
            return False

    def _needs_rewrite(q) -> bool:
        if q.needs_retry or has_bad_mt(current):
            return True
        if lang == "uk":
            try:
                from engines.naturalizer_v2.literary_uk import should_force_literary_llm

                # Literary LLM only when Extended reserve OR quality still bad
                if should_force_literary_llm(
                    current,
                    original=original,
                    quality_needs_retry=bool(q.needs_retry),
                ):
                    if q.needs_retry or has_bad_mt(current) or _catp_mode_allows_extended():
                        return True
            except Exception:
                pass
        try:
            from engines.mt.dirty_mt import compute_dirty_mt_score

            if compute_dirty_mt_score(original, current, tgt_lang=tgt_lang).dirty:
                return True
            # Raw was dirty and rules only cosmetically touched it
            if (
                compute_dirty_mt_score(original, raw, tgt_lang=tgt_lang).dirty
                and current.strip()
                and (
                    current == raw
                    or abs(len(current) - len(raw)) < max(12, int(len(raw) * 0.08))
                )
            ):
                return True
        except Exception:
            pass
        return False

    # Phase 5 — LLM full rewrite when quality bad OR UK literary stiffness (Extended)
    if use_llm and _needs_rewrite(report):
        from engines.proper_nouns_dict import extra_preserved_tokens

        preserved = extra_preserved_tokens(original, app_dir=base_dir) if original else []
        problems = list(report.problems or [])
        if lang == "uk":
            try:
                from engines.naturalizer_v2.literary_uk import detect_stiffness

                stiff = detect_stiffness(current)
                if stiff:
                    problems = problems + [f"literary_stiff:{c}" for c in stiff[:6]]
            except Exception:
                pass
        llm_out = rewrite_segment_llm(
            current,
            original=original,
            raw_mt=raw,
            tgt_lang=tgt_lang,
            src_lang=src_lang,
            prev_context=prev_context,
            problems=problems,
            preserved_entities=preserved,
            literary=lang == "uk" and _catp_mode_allows_extended(),
        )
        if llm_out:
            accepted = accept_naturalizer_change(raw, llm_out, original=original)
            if accepted != raw:
                current = clean_punctuation(accepted)
                literary_candidate = literary_candidate or current
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
            literary=lang == "uk",
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

    # Phase 7 — CATP: Meaning Polish + Length Predictor gate (variants A/B/C)
    if lang == "uk":
        try:
            from engines.naturalizer_v2.catp import polish_with_budget, try_dsal_compress

            lit_for_catp = literary_candidate or (
                current if current != safe_baseline else None
            )
            catp = polish_with_budget(
                baseline=raw,
                safe=safe_baseline,
                literary=lit_for_catp if lit_for_catp != safe_baseline else None,
                slot_ms=int(slot_ms or 0),
                reserve_ms=reserve_ms,
                lang=lang,
            )
            catp_meta = catp.to_dict()
            if catp.text and catp.text != current:
                if catp.rollback_due_to_length:
                    reasons.append("catp_rollback_length")
                reasons.append(f"catp_{catp.selected_variant}")
                reasons.extend([r for r in catp.reasons if r.startswith("catp_")])
                current = catp.text
                fix_count += 1
            elif catp.selected_variant:
                reasons.append(f"catp_{catp.selected_variant}")
                reasons.extend(list(catp.reasons[:4]))

            if catp.handoff_to_dsal and int(slot_ms or 0) > 0:
                compressed, ok = try_dsal_compress(
                    current,
                    slot_ms=int(slot_ms),
                    lang=lang,
                    source_hint=original,
                )
                if ok and compressed != current:
                    current = compressed
                    reasons.append("catp_dsal_handoff")
                    catp_meta["dsal_handoff_applied"] = True
                    fix_count += 1
                else:
                    catp_meta["dsal_handoff_applied"] = False
                    warnings.append("catp_handoff_to_dsal")
        except Exception as exc:
            logger.debug("CATP skipped: %s", exc)
            warnings.append(f"catp_error:{exc}")

    # Guarantee entity restoration survives CATP. CATP builds its variants from
    # the raw/safe baselines, which may still carry mask tokens (e.g. TITLE_SW_1)
    # when the restoration happened after the snapshot — a rollback would then leak
    # the raw token into the dub. Re-apply restoration as the final, idempotent
    # step so masks never reach output.
    if entity_token_map:
        current, re_restored = restore_entities(
            current,
            entity_token_map,
            original=original,
            tgt_lang=tgt_lang,
            app_dir=base_dir,
        )
        if re_restored:
            if "restored_entities" not in reasons:
                reasons.append("restored_entities")
            for ent in re_restored:
                if ent not in restored:
                    restored.append(ent)

    # If the polished text is identical to the raw input, nothing effectively
    # changed — surface a clean ["no_changes"] instead of CATP bookkeeping noise
    # (catp_variant/catp_mode). CATP details remain in the returned `catp` meta.
    non_bookkeeping = [
        r for r in reasons if not r.startswith("catp_") and r != "no_changes"
    ]
    if current == raw and not non_bookkeeping:
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
        "catp": catp_meta,
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
        "catp": {},
    }
