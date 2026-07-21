"""Translation Core engine — Meaning → Translation → Validation → Semantic Lock.

Invariants: knows only SemanticSentence. Never Scheduler / Dub / TTS / Merge / Studio.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.types import SemanticSentence
from engines.translation_core.evaluator import evaluate_variant
from engines.translation_core.lock import (
    build_translation_context,
    lock_translated_sentence,
)
from engines.translation_core.registry import get_backend, list_backends
from engines.translation_core.rewrite import make_variant_seeds, safe_rewrite
from engines.translation_core.terminology import TerminologyManager
from engines.translation_core.types import (
    SentenceTranslationReport,
    TranslationCoreResult,
    TranslationVariant,
)
from engines.translation_core.validators import completeness_score, hallucination_score

logger = logging.getLogger("tubedub.translation_core.engine")

FORBIDDEN_UNITS = frozenset(
    {"whisper_segment", "chunk", "window", "buffer", "audio_slot", "segment"}
)


def assert_sentence_only(unit: Any) -> None:
    """P203 — Native Sentence Translation only."""
    if isinstance(unit, SemanticSentence):
        return
    ut = getattr(unit, "unit_type", None) or (
        unit.get("unit_type") if isinstance(unit, dict) else None
    )
    if ut in FORBIDDEN_UNITS:
        raise ArchitectureViolation(
            f"P203: forbidden translation unit {ut!r}",
            stage="translation_core",
            rule="sentence_only",
        )
    raise ArchitectureViolation(
        "P203: Translation Core accepts SemanticSentence only",
        stage="translation_core",
        rule="sentence_only",
    )


def max_variants() -> int:
    try:
        return max(1, min(8, int(os.environ.get("VM_TRANSLATION_VARIANTS", "4"))))
    except ValueError:
        return 4


def min_similarity() -> float:
    try:
        return float(os.environ.get("VM_TRANSLATION_MIN_SIMILARITY", "0.55"))
    except ValueError:
        return 0.55


def translate_sentences(
    sentences: list[SemanticSentence],
    *,
    src_lang: str,
    tgt_lang: str,
    backend_id: str | None = None,
    terminology: TerminologyManager | None = None,
    lock: bool = True,
    allow_rewrite: bool = True,
) -> TranslationCoreResult:
    """
    Full Translation Core path:
    SemanticSentence → Backend (multi-pass) → Validate → Evaluate → Lock → Report
    """
    for s in sentences:
        assert_sentence_only(s)

    backend = get_backend(backend_id)
    terms = terminology or TerminologyManager()
    reports: list[SentenceTranslationReport] = []
    n_var = max_variants()
    sim_thr = min_similarity()

    for i, sent in enumerate(sentences):
        src = (sent.text or "").strip()
        if not src:
            reports.append(
                SentenceTranslationReport(
                    sentence_uuid=sent.sentence_uuid,
                    source_text="",
                    selected_text="",
                    selection_reason="empty",
                )
            )
            continue

        ctx = build_translation_context(sentences, i)
        seeds = make_variant_seeds(src)[:n_var]
        variants: list[TranslationVariant] = []

        for label, seed in seeds:
            try:
                raw = backend.translate(
                    seed,
                    src_lang=src_lang,
                    tgt_lang=tgt_lang,
                    context=ctx,
                )
            except Exception as exc:
                logger.warning("backend %s failed: %s", backend.id, exc)
                raw = seed
            text = terms.apply(str(raw or seed))
            if allow_rewrite and not sent.semantic_locked:
                text = safe_rewrite(
                    src,
                    text,
                    entities=list(sent.entities or []),
                    locked=False,
                    min_similarity=sim_thr,
                )

            comp = completeness_score(src, text, list(sent.entities or []))
            hall_score, hall_warn = hallucination_score(src, text)
            term_map = terms.all_terms()
            hits = sum(1 for k in term_map if k.lower() in text.lower())
            scores = evaluate_variant(
                src,
                text,
                entities=list(sent.entities or []),
                style=str(ctx.get("style") or ""),
                emotion=str(ctx.get("emotion") or ""),
                terminology_hits=hits,
                terminology_total=max(1, len(term_map)) if term_map else 0,
                has_context=bool(ctx.get("prev") or ctx.get("next") or ctx.get("dialogue_id")),
                completeness=comp,
            )
            # Blend hallucination into completeness/meaning
            if hall_score < 0.7:
                scores.completeness = min(scores.completeness, hall_score * 100)
                scores.meaning = min(scores.meaning, hall_score * 100)

            var = TranslationVariant(
                label=label,
                text=text,
                backend_id=backend.id,
                scores=scores,
                warnings=list(hall_warn),
            )
            # Reject gates (entity must be 100%; hallucination hard-fail)
            if scores.entity < 100:
                var.rejected = True
                var.reject_reasons.append("entity_preservation")
            if hall_score < 0.7:
                var.rejected = True
                var.reject_reasons.append("hallucination")
            if comp < 0.55:
                var.rejected = True
                var.reject_reasons.append("completeness")
            # Similarity gate only when same-script high-overlap expected fails badly
            if scores.similarity < sim_thr * 0.5 and scores.entity >= 100:
                var.warnings.append(f"low_similarity={scores.similarity}")
            variants.append(var)

        # Decision Layer: best non-rejected by confidence, else best overall
        alive = [v for v in variants if not v.rejected]
        pool = alive or variants
        best = max(pool, key=lambda v: (v.scores.confidence, v.scores.average()))
        reason = (
            f"best_confidence={best.scores.confidence:.3f};"
            f"avg={best.scores.average():.1f};"
            f"label={best.label}"
        )
        if best.rejected and alive:
            best = max(alive, key=lambda v: v.scores.confidence)
            reason = "fallback_alive:" + reason

        if lock:
            lock_translated_sentence(sent, best.text)
        else:
            sent.translated_text = best.text
            sent.translation_status = "translated"

        report = SentenceTranslationReport(
            sentence_uuid=sent.sentence_uuid,
            source_text=src,
            selected_variant_id=best.variant_id,
            selected_text=best.text,
            selected_label=best.label,
            selection_reason=reason,
            variants=variants,
            warnings=list(best.warnings),
            locked=bool(sent.semantic_locked),
            confidence=best.scores.confidence,
        )
        reports.append(report)
        # Attach report on sentence context (explainability)
        sent.context = {
            **(sent.context or {}),
            "translation_report": report.to_dict(),
        }

    return TranslationCoreResult(
        sentences=sentences,
        reports=reports,
        backend_id=backend.id,
        locked=lock,
    )


def translation_core_info() -> dict[str, Any]:
    return {
        "module": "translation_core",
        "spec": "Master Spec Part 3 v6.0",
        "backends": list_backends(),
        "max_variants": max_variants(),
        "min_similarity": min_similarity(),
        "invariants": [
            "no_scheduler",
            "no_dub_engine",
            "no_tts",
            "no_merge",
            "no_studio",
            "sentence_only",
        ],
    }
