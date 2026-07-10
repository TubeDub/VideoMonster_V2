"""Full quality gate for one segment."""

from __future__ import annotations

from typing import Any

from engines.ai_core.quality_agent.scoring import (
    QualityScores,
    SegmentAuditResult,
    compute_overall,
)
from engines.ai_core.quality_agent.validators.emotion_check import check_emotion
from engines.ai_core.quality_agent.validators.entity_check import check_entity
from engines.ai_core.quality_agent.validators.grammar_check import check_grammar
from engines.ai_core.quality_agent.validators.language_check import check_language
from engines.ai_core.quality_agent.validators.meaning_check import check_meaning
from engines.ai_core.quality_agent.validators.natural_speech_check import check_natural_speech
from engines.ai_core.quality_agent.validators.sentence_integrity import check_sentence_integrity
from engines.ai_core.quality_agent.validators.slot_fit_check import check_slot_fit
from engines.ai_core.quality_agent.validators.syntax_check import check_syntax
from engines.ai_core.quality_agent.validators.terminology_check import check_terminology
from engines.ai_core.quality_agent.validators.timing_check import check_timing
from engines.ai_core.quality_agent.validators.voice_readiness_check import check_voice_readiness

_NULL_MARKERS = frozenset({"null", "\x00", "none"})


def _reasons(result) -> list[str]:
    return list(getattr(result, "reasons", None) or getattr(result, "issues", None) or [])


def _is_critical(segment: dict) -> tuple[bool, list[str]]:
    """Critical FAIL: empty segment, corrupted text, NULL."""
    reasons: list[str] = []
    grammar = str(segment.get("grammar_text") or "").strip()
    if "grammar_text" in segment and not grammar:
        return True, ["empty_grammar_text"]

    candidate = str(
        grammar
        or segment.get("timing_text")
        or segment.get("semantic_text")
        or segment.get("translated_text")
        or ""
    ).strip()

    if not candidate:
        return True, ["empty_segment"]

    lower = candidate.lower()
    if lower in _NULL_MARKERS or candidate == "\x00":
        return True, ["null_segment"]

    if len(candidate) < 2 and not candidate.isalnum():
        return True, ["corrupted_text"]

    return False, reasons


def audit_segment(
    segment: dict,
    *,
    all_segments: list[dict],
    source_lang: str = "en",
    target_lang: str = "ru",
    timing_report: dict[str, Any] | None = None,
    brief_thresholds: dict[str, float] | None = None,
) -> SegmentAuditResult:
    """Run all quality checks on a single segment (read-only)."""
    idx = int(segment.get("index", 0))
    source = str(segment.get("text") or "").strip()
    translated = str(segment.get("translated_text") or "").strip()
    semantic = str(segment.get("semantic_text") or translated).strip()
    timing = str(segment.get("timing_text") or semantic).strip()
    grammar = str(segment.get("grammar_text") or timing).strip()
    reference = timing or semantic or translated

    critical, critical_reasons = _is_critical(segment)
    if critical:
        scores = QualityScores(
            overall=0.0,
            meaning=0.0,
            grammar=0.0,
            timing=0.0,
            naturalness=0.0,
            emotion=0.0,
            voice_readiness=0.0,
            entity=0.0,
            syntax=0.0,
        )
        return SegmentAuditResult(
            index=idx,
            scores=scores,
            failure_types=["critical"],
            reasons=critical_reasons,
            critical=True,
        )

    checks: dict[str, dict] = {}
    failure_types: list[str] = []
    all_reasons: list[str] = []

    meaning_r = check_meaning(source, translated, grammar)
    checks["meaning"] = {"ok": meaning_r.ok, "score": meaning_r.score, "reasons": _reasons(meaning_r)}
    if meaning_r.failure_type:
        failure_types.append(meaning_r.failure_type)
    all_reasons.extend(_reasons(meaning_r))

    entity_r = check_entity(source, grammar)
    checks["entity"] = {"ok": entity_r.ok, "score": entity_r.score, "reasons": _reasons(entity_r)}
    if entity_r.failure_type:
        failure_types.append(entity_r.failure_type)
    all_reasons.extend(_reasons(entity_r))

    term_r = check_terminology(all_segments)
    checks["terminology"] = {"ok": term_r.ok, "score": term_r.score, "reasons": _reasons(term_r)}
    if term_r.failure_type:
        failure_types.append(term_r.failure_type)
    all_reasons.extend(_reasons(term_r))

    lang_r = check_language(source, grammar, source_lang=source_lang, target_lang=target_lang)
    checks["language"] = {"ok": lang_r.ok, "score": lang_r.score, "reasons": _reasons(lang_r)}
    if lang_r.failure_type:
        failure_types.append(lang_r.failure_type)
    all_reasons.extend(_reasons(lang_r))

    grammar_r = check_grammar(grammar, tgt_lang=target_lang)
    checks["grammar"] = {"ok": grammar_r.ok, "score": grammar_r.score, "reasons": _reasons(grammar_r)}
    if grammar_r.failure_type:
        failure_types.append(grammar_r.failure_type)
    all_reasons.extend(_reasons(grammar_r))

    syntax_r = check_syntax(grammar)
    checks["syntax"] = {"ok": syntax_r.ok, "score": syntax_r.score, "reasons": _reasons(syntax_r)}
    if syntax_r.failure_type:
        failure_types.append(syntax_r.failure_type)
    all_reasons.extend(_reasons(syntax_r))

    natural_r = check_natural_speech(grammar, tgt_lang=target_lang)
    checks["natural_speech"] = {"ok": natural_r.ok, "score": natural_r.score, "reasons": _reasons(natural_r)}
    if natural_r.failure_type:
        failure_types.append(natural_r.failure_type)
    all_reasons.extend(_reasons(natural_r))

    emotion_r = check_emotion(source, translated, grammar)
    checks["emotion"] = {"ok": emotion_r.ok, "score": emotion_r.score, "reasons": _reasons(emotion_r)}
    all_reasons.extend(_reasons(emotion_r))

    timing_entry = None
    if timing_report:
        for row in timing_report.get("per_segment") or []:
            if int(row.get("index", -1)) == idx:
                timing_entry = row
                break

    timing_r = check_timing(grammar, segment, tgt_lang=target_lang, timing_entry=timing_entry)
    checks["timing"] = {"ok": timing_r.ok, "score": timing_r.score, "reasons": _reasons(timing_r)}
    if timing_r.failure_type:
        failure_types.append(timing_r.failure_type)
    all_reasons.extend(_reasons(timing_r))

    slot_r = check_slot_fit(grammar, segment, tgt_lang=target_lang, timing_entry=timing_entry)
    checks["slot_fit"] = {"ok": slot_r.ok, "score": slot_r.score, "reasons": _reasons(slot_r)}
    if slot_r.failure_type and slot_r.failure_type not in failure_types:
        failure_types.append(slot_r.failure_type)
    all_reasons.extend(_reasons(slot_r))

    integrity_r = check_sentence_integrity(reference, grammar)
    checks["sentence_integrity"] = {
        "ok": integrity_r.ok,
        "score": integrity_r.score,
        "reasons": _reasons(integrity_r),
    }
    if integrity_r.failure_type:
        failure_types.append(integrity_r.failure_type)
    all_reasons.extend(_reasons(integrity_r))

    voice_r = check_voice_readiness(grammar)
    checks["voice_readiness"] = {"ok": voice_r.ok, "score": voice_r.score, "reasons": _reasons(voice_r)}
    if voice_r.failure_type:
        failure_types.append(voice_r.failure_type)
    all_reasons.extend(_reasons(voice_r))

    timing_score = max(timing_r.score, slot_r.score)
    naturalness_score = (natural_r.score + grammar_r.score) / 2.0

    overall = compute_overall(
        meaning=meaning_r.score,
        grammar=grammar_r.score,
        timing=timing_score,
        naturalness=naturalness_score,
        emotion=emotion_r.score,
        voice_readiness=voice_r.score,
        entity=entity_r.score,
        syntax=syntax_r.score,
    )

    scores = QualityScores(
        overall=overall,
        meaning=meaning_r.score,
        grammar=grammar_r.score,
        timing=timing_score,
        naturalness=naturalness_score,
        emotion=emotion_r.score,
        voice_readiness=voice_r.score,
        entity=entity_r.score,
        syntax=syntax_r.score,
    )

    if brief_thresholds:
        min_meaning = float(brief_thresholds.get("meaning_priority", 0.0))
        min_natural = float(brief_thresholds.get("naturalness_priority", 0.0))
        min_lip = float(brief_thresholds.get("lip_sync_priority", 0.0))
        if min_meaning and meaning_r.score < min_meaning:
            failure_types.append("brief_meaning_below_threshold")
        if min_natural and naturalness_score < min_natural:
            failure_types.append("brief_naturalness_below_threshold")
        if min_lip and timing_score < min_lip:
            failure_types.append("brief_lipsync_below_threshold")

    return SegmentAuditResult(
        index=idx,
        scores=scores,
        checks=checks,
        failure_types=failure_types,
        reasons=all_reasons[:20],
        critical=False,
    )
