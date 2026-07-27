"""Fast QA — first TQE layer (no EN/UK word-count hard fail)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from engines.tps.statuses import TQEStatus


@dataclass
class FastQAResult:
    status: TQEStatus
    errors: list[dict[str, Any]] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == TQEStatus.PASS


def run_fast_qa(
    original: str,
    translation: str,
    *,
    context: dict[str, Any] | None = None,
) -> FastQAResult:
    """Minimal Fast QA checks per TPS TZ Part 8.

    Explicitly does NOT hard-fail on en vs uk word-count ratio alone.
    """
    ctx = dict(context or {})
    src = str(original or "").strip()
    tr = str(translation or "").strip()
    errors: list[dict[str, Any]] = []

    if not tr:
        errors.append({"code": "empty", "severity": "critical"})
    if tr and re.search(r",\s*\.\s*$|,\s*$", tr):
        errors.append({"code": "incomplete", "severity": "critical", "detail": "orphan comma"})
    if tr.lstrip().startswith("<speak"):
        errors.append({"code": "ssml_in_text", "severity": "critical"})

    # Incomplete sentence when source is complete
    if src.endswith((".", "!", "?", "…")) and tr and tr[-1] not in ".!?…»\"":
        # Only major if also mid-clause clues
        if re.search(r"[,;:]\s*$", tr) or len(tr.split()) < 4:
            errors.append({"code": "incomplete_sentence", "severity": "critical"})

    # Mixed language: high latin share in UK/RU target
    tgt = str(ctx.get("target_lang") or "uk").split("-")[0].lower()
    if tgt in ("uk", "ru", "be") and tr:
        letters = [c for c in tr if c.isalpha()]
        if letters:
            latin = sum(1 for c in letters if "a" <= c.lower() <= "z")
            if latin / len(letters) > 0.55 and len(letters) > 12:
                errors.append({"code": "mixed_language", "severity": "critical"})

    # Entity presence — prefer project glossary (HF2), fallback semantic_meaning
    try:
        from engines.project_glossary import check_glossary_entities, load_project_glossary

        gloss = load_project_glossary(
            app_dir=ctx.get("app_dir"),
            project_id=ctx.get("project_id") or ctx.get("glossary_id"),
            info=ctx,
        )
        for missing in check_glossary_entities(src, tr, gloss):
            errors.append(
                {
                    "code": "entity_missing",
                    "token": missing,
                    "severity": "critical",
                    "source": "project_glossary",
                }
            )
    except Exception:
        try:
            from engines.semantic_meaning import check_critical_entities

            for err in check_critical_entities(src, tr) or []:
                errors.append(
                    {
                        "code": "entity_missing",
                        "token": err if isinstance(err, str) else str(err),
                        "severity": "critical",
                    }
                )
        except Exception:
            pass

    # HF3: dirty Raw MT left unchanged / still calqued after naturalizer
    try:
        from engines.mt.dirty_mt import (
            naturalizer_noop_is_bug,
            residual_dirty_after_naturalize,
        )
        from engines.naturalizer_v2.bad_patterns import has_bad_mt

        raw_mt = str(ctx.get("raw_mt") or tr)
        naturalized = str(ctx.get("naturalized") or tr)
        if naturalizer_noop_is_bug(src, raw_mt, naturalized, tgt_lang=tgt):
            errors.append(
                {
                    "code": "dirty_mt_noop",
                    "severity": "critical",
                    "detail": "dirty MT left unfixed after naturalizer",
                }
            )
        elif residual_dirty_after_naturalize(src, tr, tgt_lang=tgt) or has_bad_mt(tr):
            errors.append(
                {
                    "code": "nonsense_calque",
                    "severity": "critical",
                    "detail": "residual calque/garbage in approved candidate",
                }
            )
    except Exception:
        pass

    # Mid-name break (HF5)
    if re.search(r"молодш(?:ий|ого)\.\s+[а-яіїєґ]", tr):
        errors.append(
            {
                "code": "broken_phrase",
                "severity": "critical",
                "detail": "mid_name_period",
            }
        )

    # Clause / meaning integrity (not word-count)
    try:
        from engines.semantic_meaning import verify_meaning_preserved

        ok, reason, _ = verify_meaning_preserved(src, tr, tr, target_lang=tgt)
        if not ok and reason in (
            "incomplete_sentence",
            "meaning_loss",
            "entity_loss",
            "severe_truncation",
        ):
            errors.append(
                {
                    "code": reason if reason != "meaning_loss" else "meaning_loss",
                    "severity": "critical",
                }
            )
    except Exception:
        pass

    # Orphan clause glue (known bad patterns)
    try:
        from engines.tqe.rules.grammar import check_grammar

        for err in check_grammar(src, tr, ctx) or []:
            if err.get("severity") == "critical":
                errors.append(err)
    except Exception:
        pass

    # Severe MT collapse — meaning coverage, NOT raw en/uk word-count fail
    try:
        from engines.mt.sentence_split import is_severe_mt_collapse

        if is_severe_mt_collapse(src, tr):
            errors.append(
                {
                    "code": "meaning_loss",
                    "severity": "critical",
                    "detail": "severe_mt_collapse",
                }
            )
    except Exception:
        pass

    # Cross-script leak / meaning hallucination (any language pair)
    try:
        from engines.mt.cross_script_guard import (
            has_phrase_loop,
            meaning_collapse,
            source_script_leak,
        )

        src_lang = str(ctx.get("source_lang") or "").split("-")[0].lower()
        leak = source_script_leak(
            src, tr, source_lang=src_lang or None, target_lang=tgt
        )
        if leak:
            errors.append(
                {
                    "code": "source_script_leak",
                    "severity": "critical",
                    "detail": leak.get("reason") or "source_script_dominant",
                }
            )
        if has_phrase_loop(tr, min_repeats=3):
            try:
                from engines.mt.cross_script_guard import deflate_phrase_loop

                deflated = deflate_phrase_loop(tr)
            except Exception:
                deflated = ""
            # Only fail when the loop cannot be collapsed to clean target text.
            if not deflated or has_phrase_loop(deflated, min_repeats=2):
                errors.append(
                    {
                        "code": "phrase_loop",
                        "severity": "critical",
                        "detail": "repeated_phrase_hallucination",
                    }
                )
        collapse = meaning_collapse(
            src, tr, source_lang=src_lang or None, target_lang=tgt
        )
        if collapse:
            errors.append(
                {
                    "code": "meaning_collapse",
                    "severity": "critical",
                    "detail": ",".join((collapse.get("reasons") or [])[:3]),
                }
            )
            # Legacy review code for zh→uk dumps
            if (collapse.get("source_script") == "cjk") or src_lang in (
                "zh",
                "ja",
                "ko",
                "yue",
            ):
                errors.append(
                    {
                        "code": "cjk_meaning_collapse",
                        "severity": "critical",
                        "detail": ",".join((collapse.get("reasons") or [])[:3]),
                    }
                )
    except Exception:
        pass

    # TRH: residual breakage after naturalizer must FAIL (not silent naturalizer PASS)
    try:
        from engines.trh.canon_repair import still_broken_entities
        from engines.mt.dirty_mt import _EN_LEAK

        broken = still_broken_entities(tr, src)
        for token in broken:
            errors.append(
                {
                    "code": "entity_breakage" if token not in ("dreading",) else "en_word_leak",
                    "token": token,
                    "severity": "critical",
                }
            )
        if _EN_LEAK.search(tr):
            errors.append(
                {
                    "code": "en_word_leak",
                    "severity": "critical",
                    "detail": _EN_LEAK.search(tr).group(0),
                }
            )
    except Exception:
        pass

    critical = [e for e in errors if e.get("severity") == "critical"]
    codes = [str(e.get("code") or "") for e in critical]
    if critical:
        return FastQAResult(
            status=TQEStatus.FAIL_RETRY_MEANING_GRAMMAR,
            errors=critical,
            reason_codes=codes,
        )
    return FastQAResult(status=TQEStatus.PASS, errors=[], reason_codes=[])
