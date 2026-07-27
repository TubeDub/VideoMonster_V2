# -*- coding: utf-8 -*-
"""Unified Language Validation interface for TubeDub pipeline gates.

Separates true language/script mismatch from semantic collapse (phrase_loop /
meaning_collapse). Uses confidence scores over the full text after entity masking.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.language_validation.confidence import (
    neighbor_language_vote,
    score_language_confidence,
)
from engines.language_validation.entities import mask_entities

# Confidence thresholds (TZ: probabilistic, not binary)
_HARD_PASS = 0.72
_SOFT_PASS = 0.45
_HARD_FAIL_LANG = 0.55  # foreign lang must beat this to hard-fail


@dataclass
class LanguageValidationDecision:
    ok: bool
    hard_fail: bool
    category: str  # pass | language_mismatch | meaning_collapse | phrase_loop | ambiguous | low_confidence
    code: str
    expected_lang: str
    detected_lang: str
    confidence: float
    target_confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    text_checked: str = ""
    text_masked: str = ""
    entities_masked: list[str] = field(default_factory=list)
    stage: str = ""
    module: str = "engines.language_validation.service"
    validator: str = "unified_language_validation"
    index: int | None = None
    segment_id: str | None = None
    recovery_actions: list[str] = field(default_factory=list)
    decision_trace: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    collapse: dict[str, Any] | None = None
    script_mismatch: bool = False
    final_preview: str = ""

    def to_issue(self) -> dict[str, Any]:
        """Legacy issue dict for validate_segments_target_language callers."""
        return {
            "index": self.index,
            "segment_id": self.segment_id,
            "code": self.code,
            "category": self.category,
            "detected_lang": self.detected_lang,
            "target_lang": self.expected_lang,
            "confidence": self.confidence,
            "target_confidence": self.target_confidence,
            "scores": dict(self.scores),
            "final_preview": self.final_preview or self.text_checked[:200],
            "reasons": list(self.reasons),
            "hard_fail": self.hard_fail,
            "ok": self.ok,
            "message": self.message,
            "decision_trace": list(self.decision_trace),
            "entities_masked": list(self.entities_masked),
            "collapse": self.collapse,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def format_validation_message(d: LanguageValidationDecision) -> str:
    """Human-readable diagnostic (RU) per TZ task 10."""
    lines = [
        "Language Validation failed." if not d.ok else "Language Validation passed.",
        f"Expected language: {d.expected_lang}",
        f"Detected: {d.detected_lang} (confidence {d.confidence:.2f})",
        f"Target confidence: {d.target_confidence:.2f}",
        f"Category: {d.category}",
        f"Code: {d.code or 'ok'}",
        f"Module: {d.module}",
        f"Validator: {d.validator}",
    ]
    if d.stage:
        lines.append(f"Stage: {d.stage}")
    if d.reasons:
        lines.append("Причина: " + "; ".join(d.reasons[:6]))
    if d.entities_masked:
        lines.append(
            "Masked entities: " + ", ".join(d.entities_masked[:12])
        )
    if d.recovery_actions:
        lines.append("Recovery: " + " → ".join(d.recovery_actions))
    if d.text_checked:
        lines.append(f"Text: {d.text_checked[:160]}")
    return "\n".join(lines)


def _base(lang: str) -> str:
    return str(lang or "").strip().lower().split("-")[0]


def validate_language(
    text: str,
    *,
    target_lang: str,
    original: str = "",
    source_lang: str = "",
    stage: str = "",
    index: int | None = None,
    segment_id: str | None = None,
    neighbor_texts: list[str] | None = None,
    allow_semantic_soft_pass: bool = True,  # reserved: recovery soft-continue policy
) -> LanguageValidationDecision:
    """Single Language Validation entry point.

    Decision order:
      1. Mask entities → score full text
      2. Script / critical mismatch (true LANGUAGE_MISMATCH)
      3. Ambiguous confidence → recheck + backup + neighbors
      4. Semantic collapse (phrase_loop / meaning_collapse) — NOT language mismatch
      5. Hard-fail only when language truly wrong OR unrecoverable collapse with high certainty
    """
    expected = _base(target_lang) or "uk"
    raw = str(text or "").strip()
    trace: list[dict[str, Any]] = []
    recovery: list[str] = []

    trace.append(
        {
            "step": "input",
            "expected": expected,
            "text_len": len(raw),
            "stage": stage or "",
        }
    )

    if not raw:
        d = LanguageValidationDecision(
            ok=True,
            hard_fail=False,
            category="pass",
            code="",
            expected_lang=expected,
            detected_lang="empty",
            confidence=1.0,
            target_confidence=1.0,
            stage=stage,
            index=index,
            segment_id=segment_id,
            decision_trace=trace,
            message="Empty segment skipped",
        )
        return d

    masked, entities = mask_entities(raw)
    trace.append(
        {
            "step": "entity_mask",
            "entities": entities[:20],
            "masked_preview": masked[:160],
        }
    )

    scores_primary = score_language_confidence(
        masked, target_lang=expected, masked=True
    )
    # Also score original (for reporting) but decisions use masked
    scores_raw = score_language_confidence(raw, target_lang=expected, masked=False)
    trace.append(
        {
            "step": "confidence_primary",
            "scores": scores_primary.get("scores"),
            "detected": scores_primary.get("detected"),
            "confidence": scores_primary.get("confidence"),
            "target_confidence": scores_primary.get("target_confidence"),
            "backup": scores_primary.get("backup"),
            "raw_detected": scores_raw.get("detected"),
            "raw_confidence": scores_raw.get("confidence"),
        }
    )

    detected = str(scores_primary.get("detected") or "unknown")
    confidence = float(scores_primary.get("confidence") or 0.0)
    target_conf = float(scores_primary.get("target_confidence") or 0.0)
    score_map = dict(scores_primary.get("scores") or {})

    # --- True script / language mismatch via existing gate helpers ---
    script_bad = False
    script_code = ""
    try:
        from engines.pipeline_language_gate import is_critical_language_mismatch

        script_bad, script_code = is_critical_language_mismatch(
            masked,
            target_lang=target_lang,
            original=original,
            source_lang=source_lang,
        )
        # Re-check on raw if masked cleared brands and still fails on raw only for latin
        if script_bad and "latin" in script_code and entities:
            script_bad2, script_code2 = is_critical_language_mismatch(
                masked,
                target_lang=target_lang,
                original=original,
                source_lang=source_lang,
            )
            script_bad, script_code = script_bad2, script_code2
    except Exception:
        pass

    trace.append(
        {
            "step": "script_gate",
            "bad": script_bad,
            "code": script_code,
        }
    )

    # Ambiguous band → recheck + neighbors
    if (
        not script_bad
        and _SOFT_PASS <= target_conf < _HARD_PASS
        and detected != expected
    ):
        recovery.append("recheck_confidence")
        # Prefer raw Cyrillic boost toward target when script is Cyrillic
        counts = scores_primary.get("counts") or {}
        if int(counts.get("cyrillic") or 0) >= 8 and expected in ("uk", "ru", "be"):
            detected = expected
            target_conf = max(target_conf, 0.7)
            confidence = max(confidence, target_conf)
            recovery.append("cyrillic_target_align")
            trace.append(
                {
                    "step": "cyrillic_target_align",
                    "detected": detected,
                    "target_confidence": target_conf,
                }
            )

        if neighbor_texts:
            recovery.append("neighbor_context")
            vote = neighbor_language_vote(neighbor_texts, target_lang=expected)
            trace.append({"step": "neighbor_vote", **vote})
            if vote.get("detected") == expected and float(vote.get("confidence") or 0) >= 0.5:
                detected = expected
                target_conf = max(target_conf, float(vote["confidence"]))
                confidence = max(confidence, target_conf)
                recovery.append("neighbor_confirmed_target")

    # CRITICAL TZ rule: expected == detected → never Language Mismatch
    lang_match = detected == expected or (
        expected in ("uk", "ru", "be")
        and detected in ("uk", "ru", "be")
        and target_conf >= _SOFT_PASS
        and int((scores_primary.get("counts") or {}).get("cyrillic") or 0) >= 8
    )

    if script_bad and lang_match and "cjk" not in script_code and "arabic" not in script_code:
        # Brands made latin_dominant false positive — clear when Cyrillic solid
        if "latin" in script_code or "english" in script_code:
            script_bad = False
            script_code = ""
            recovery.append("cleared_entity_latin_false_positive")
            trace.append({"step": "clear_latin_fp", "entities": entities[:10]})

    if script_bad and not lang_match and target_conf < _HARD_FAIL_LANG:
        # Weak foreign signal → ambiguous, not hard stop yet
        d = LanguageValidationDecision(
            ok=False,
            hard_fail=False,
            category="ambiguous",
            code=script_code or "low_confidence_language",
            expected_lang=expected,
            detected_lang=detected,
            confidence=confidence,
            target_confidence=target_conf,
            scores=score_map,
            reasons=[
                "низкая уверенность классификатора",
                f"script_gate:{script_code}",
            ],
            text_checked=raw,
            text_masked=masked,
            entities_masked=entities,
            stage=stage,
            index=index,
            segment_id=segment_id,
            recovery_actions=recovery + ["needs_recovery_pipeline"],
            decision_trace=trace,
            script_mismatch=True,
            final_preview=raw[:200],
        )
        d.message = format_validation_message(d)
        return d

    if script_bad and (
        (detected != expected and confidence >= _HARD_FAIL_LANG)
        or "cjk" in script_code
        or "arabic" in script_code
        or "source_script_leak" in script_code
    ):
        d = LanguageValidationDecision(
            ok=False,
            hard_fail=True,
            category="language_mismatch",
            code=script_code or "language_mismatch",
            expected_lang=expected,
            detected_lang=detected,
            confidence=confidence,
            target_confidence=target_conf,
            scores=score_map,
            reasons=[f"script_mismatch:{script_code}"],
            text_checked=raw,
            text_masked=masked,
            entities_masked=entities,
            stage=stage,
            index=index,
            segment_id=segment_id,
            recovery_actions=recovery + ["needs_recovery_pipeline"],
            decision_trace=trace,
            script_mismatch=True,
            final_preview=raw[:200],
        )
        d.message = format_validation_message(d)
        return d

    # --- Semantic axis (NOT Language Mismatch when lang matches) ---
    collapse = None
    phrase_loop = False
    try:
        from engines.mt.cross_script_guard import has_phrase_loop, meaning_collapse

        phrase_loop = has_phrase_loop(raw, min_repeats=3)
        if original:
            collapse = meaning_collapse(
                original,
                raw,
                source_lang=source_lang or None,
                target_lang=target_lang,
            )
    except Exception:
        pass

    if phrase_loop or (
        collapse and "phrase_loop" in (collapse.get("reasons") or [])
    ):
        trace.append({"step": "phrase_loop", "collapse": collapse})
        d = LanguageValidationDecision(
            ok=False,
            hard_fail=False,  # auto-healable
            category="phrase_loop",
            code="phrase_loop",
            expected_lang=expected,
            detected_lang=detected if lang_match else detected,
            confidence=confidence,
            target_confidence=target_conf,
            scores=score_map,
            reasons=["phrase_loop"],
            text_checked=raw,
            text_masked=masked,
            entities_masked=entities,
            stage=stage,
            index=index,
            segment_id=segment_id,
            recovery_actions=recovery + ["deflate_phrase_loop"],
            decision_trace=trace,
            collapse=collapse,
            final_preview=raw[:200],
        )
        # TZ: expected==detected must never be reported as Language Mismatch
        d.message = format_validation_message(d)
        return d

    if collapse:
        reasons = list(collapse.get("reasons") or [])
        trace.append({"step": "meaning_collapse", "reasons": reasons})
        # Never silent-pass meta_waffle / cue loss — recovery must try first.
        # Language Mismatch is still forbidden when detected==expected.
        d = LanguageValidationDecision(
            ok=False,
            hard_fail=False,  # try recovery first
            category="meaning_collapse",
            code="meaning_collapse",
            expected_lang=expected,
            detected_lang=detected,
            confidence=confidence,
            target_confidence=target_conf,
            scores=score_map,
            reasons=reasons or ["meaning_collapse"],
            text_checked=raw,
            text_masked=masked,
            entities_masked=entities,
            stage=stage,
            index=index,
            segment_id=segment_id,
            recovery_actions=recovery
            + ["naturalizer", "meaning_fit", "salvage", "revalidate"],
            decision_trace=trace,
            collapse=collapse,
            final_preview=raw[:200],
        )
        d.message = format_validation_message(d)
        return d

    # Low target confidence without script fail
    if target_conf < _SOFT_PASS and detected != expected:
        d = LanguageValidationDecision(
            ok=False,
            hard_fail=False,
            category="low_confidence",
            code="low_confidence",
            expected_lang=expected,
            detected_lang=detected,
            confidence=confidence,
            target_confidence=target_conf,
            scores=score_map,
            reasons=["низкая уверенность классификатора"],
            text_checked=raw,
            text_masked=masked,
            entities_masked=entities,
            stage=stage,
            index=index,
            segment_id=segment_id,
            recovery_actions=recovery + ["needs_recovery_pipeline"],
            decision_trace=trace,
            final_preview=raw[:200],
        )
        d.message = format_validation_message(d)
        return d

    # Pass
    d = LanguageValidationDecision(
        ok=True,
        hard_fail=False,
        category="pass",
        code="",
        expected_lang=expected,
        detected_lang=detected if lang_match else expected,
        confidence=max(confidence, target_conf),
        target_confidence=target_conf,
        scores=score_map,
        reasons=[],
        text_checked=raw,
        text_masked=masked,
        entities_masked=entities,
        stage=stage,
        index=index,
        segment_id=segment_id,
        recovery_actions=recovery,
        decision_trace=trace + [{"step": "final", "decision": "pass"}],
        final_preview=raw[:200],
    )
    d.message = format_validation_message(d)
    return d


def validate_segments(
    segments_data: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
    target_lang: str,
    source_lang: str = "",
    stage: str = "",
    include_passes: bool = False,
) -> list[LanguageValidationDecision]:
    """Validate all segments; attach neighbor context for ambiguous rows."""
    src_rows = list(source_segments or [])
    texts: list[str] = []
    for seg in segments_data or []:
        if not isinstance(seg, dict):
            texts.append("")
            continue
        texts.append(str(seg.get("text") or seg.get("plain_text") or "").strip())

    out: list[LanguageValidationDecision] = []
    for idx, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None:
            continue
        final = texts[idx] if idx < len(texts) else ""
        if not final:
            continue
        neighbors = []
        if idx > 0:
            neighbors.append(texts[idx - 1])
        if idx + 1 < len(texts):
            neighbors.append(texts[idx + 1])
        original = src_rows[idx] if idx < len(src_rows) else str(seg.get("source_text") or "")
        decision = validate_language(
            final,
            target_lang=target_lang,
            original=original,
            source_lang=source_lang,
            stage=stage,
            index=idx,
            segment_id=str(seg.get("segment_id") or seg.get("segment_uuid") or "")
            or None,
            neighbor_texts=neighbors,
        )
        if decision.ok and not include_passes:
            continue
        if not decision.ok or include_passes:
            out.append(decision)
    return out
