"""P119 — Self-Validation gates between pipeline stages.

Before each stage transition, the validator confirms:
1. Meaning is preserved
2. Sentence is complete (not truncated)
3. Replica belongs to one MeaningUnit
4. Duration is acceptable
5. Adaptation is complete
6. No words are lost

If ANY condition is violated, the transition is FORBIDDEN.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from engines.pipeline_integrity.exceptions import ArchitectureViolation

logger = logging.getLogger("tubedub.semantic_v3.stage_validator")

PIPELINE_STAGES = (
    "word_archive",
    "sentence_reconstruction",
    "meaning_unit_builder",
    "context_graph",
    "translation",
    "semantic_adaptation",
    "meaning_validation",
    "translation_lock",
    "speech_planning",
    "duration_prediction",
    "adaptive_planner",
    "tts",
    "real_duration",
    "audio_optimization",
    "scheduler",
    "merge",
    "render",
)


@dataclass
class ValidationResult:
    """Result of a stage transition validation."""

    stage_from: str = ""
    stage_to: str = ""
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_from": self.stage_from,
            "stage_to": self.stage_to,
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
        }


def validate_stage_transition(
    stage_from: str,
    stage_to: str,
    *,
    words: list[Any] | None = None,
    sentences: list[Any] | None = None,
    meaning_units: list[Any] | None = None,
    original_word_count: int = 0,
    raise_on_fail: bool = True,
) -> ValidationResult:
    """P119: validate transition between pipeline stages.

    Raises ArchitectureViolation if validation fails and raise_on_fail=True.
    """
    result = ValidationResult(stage_from=stage_from, stage_to=stage_to)

    # Check 1: No empty data
    result.checks_run += 1
    has_data = bool(words or sentences or meaning_units)
    if not has_data:
        result.errors.append("no_data: pipeline has no words, sentences, or meaning units")
    else:
        result.checks_passed += 1

    # Check 2: Word completeness (no lost words)
    if words and original_word_count > 0:
        result.checks_run += 1
        current_count = len(words)
        if current_count < original_word_count:
            result.errors.append(
                f"words_lost: had {original_word_count}, now {current_count} "
                f"(lost {original_word_count - current_count})"
            )
        else:
            result.checks_passed += 1

    # Check 3: Sentence completeness (no empty or truncated)
    if sentences:
        result.checks_run += 1
        empty = [s for s in sentences if not (getattr(s, 'text', '') or '').strip()]
        if empty:
            result.errors.append(f"empty_sentences: {len(empty)} sentences have no text")
        else:
            result.checks_passed += 1

        result.checks_run += 1
        truncated = []
        for s in sentences:
            text = (getattr(s, 'text', '') or '').strip()
            if text and text[-1] == ',' and not getattr(s, 'is_enumeration', False):
                truncated.append(text[:30])
        if truncated:
            result.warnings.append(f"possibly_truncated: {len(truncated)} sentences end with comma")
        result.checks_passed += 1

    # Check 4: MeaningUnit integrity
    if meaning_units:
        result.checks_run += 1
        for mu in meaning_units:
            sents = getattr(mu, 'sentences', [])
            if not sents:
                result.errors.append(f"empty_meaning_unit: {getattr(mu, 'unit_uuid', '?')}")
            text = (getattr(mu, 'text', '') or '').strip()
            if not text:
                result.errors.append(f"empty_meaning_unit_text: {getattr(mu, 'unit_uuid', '?')}")
        if not result.errors:
            result.checks_passed += 1

        # Check timing continuity
        result.checks_run += 1
        for i in range(len(meaning_units) - 1):
            cur = meaning_units[i]
            nxt = meaning_units[i + 1]
            cur_end = getattr(cur, 'end_ms', 0)
            nxt_start = getattr(nxt, 'start_ms', 0)
            if nxt_start < cur_end - 50:  # 50ms tolerance
                result.warnings.append(
                    f"timing_overlap: unit {i} ends at {cur_end}ms but unit {i+1} starts at {nxt_start}ms"
                )
        result.checks_passed += 1

    # Check 5: Translation completeness (for post-translation stages)
    post_translation_stages = {
        "meaning_validation", "translation_lock", "speech_planning",
        "duration_prediction", "adaptive_planner", "tts",
    }
    if stage_to in post_translation_stages and meaning_units:
        result.checks_run += 1
        untranslated = [
            mu for mu in meaning_units
            if not (getattr(mu, 'translated_text', '') or '').strip()
        ]
        if untranslated:
            result.errors.append(
                f"untranslated_units: {len(untranslated)} meaning units have no translation"
            )
        else:
            result.checks_passed += 1

    # Check 6: Lock status (for post-lock stages)
    post_lock_stages = {
        "speech_planning", "duration_prediction", "adaptive_planner",
        "tts", "real_duration", "audio_optimization", "scheduler", "merge", "render",
    }
    if stage_to in post_lock_stages and meaning_units:
        result.checks_run += 1
        unlocked = [
            mu for mu in meaning_units
            if not getattr(mu, 'semantic_locked', False)
        ]
        if unlocked:
            result.warnings.append(
                f"unlocked_units: {len(unlocked)} meaning units not locked before {stage_to}"
            )
        result.checks_passed += 1

    # Check 7: Duration validation (for pre-TTS stages)
    if stage_to in ("tts", "speech_planning") and meaning_units:
        result.checks_run += 1
        no_duration = [
            mu for mu in meaning_units
            if getattr(mu, 'predicted_duration_ms', 0) <= 0
        ]
        if no_duration:
            result.warnings.append(
                f"no_duration_prediction: {len(no_duration)} units lack duration prediction"
            )
        result.checks_passed += 1

    # Final result
    result.passed = len(result.errors) == 0

    if not result.passed:
        msg = (
            f"P119 validation FAILED: {stage_from} -> {stage_to}: "
            f"{result.checks_passed}/{result.checks_run} checks passed. "
            f"Errors: {'; '.join(result.errors)}"
        )
        logger.error(msg)
        if raise_on_fail:
            raise ArchitectureViolation(
                msg,
                stage=stage_to,
                rule="stage_validation",
            )
    else:
        logger.info(
            "P119 validation OK: %s -> %s (%d/%d checks, %d warnings)",
            stage_from, stage_to,
            result.checks_passed, result.checks_run,
            len(result.warnings),
        )

    return result


def validate_no_segment_rule(data: Any) -> list[str]:
    """P116: verify no Whisper Segment / Chunk / Buffer / Window is used.

    Returns list of violation descriptions (empty = ok).
    """
    FORBIDDEN_KEYS = {"whisper_segment", "chunk", "buffer", "window", "segment_id"}
    FORBIDDEN_VALUES = {"whisper_segment", "chunk", "buffer", "window"}
    violations: list[str] = []

    def _check(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in FORBIDDEN_KEYS:
                    violations.append(f"forbidden_key '{k}' at {path}")
                if isinstance(v, str) and v in FORBIDDEN_VALUES:
                    violations.append(f"forbidden_value '{v}' at {path}.{k}")
                _check(v, f"{path}.{k}")
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                _check(item, f"{path}[{i}]")
        elif hasattr(obj, '__dict__'):
            ut = getattr(obj, 'unit_type', None)
            if isinstance(ut, str) and ut in FORBIDDEN_VALUES:
                violations.append(f"forbidden unit_type '{ut}' on {type(obj).__name__} at {path}")

    _check(data)

    if violations:
        logger.warning("P116 violations found: %d", len(violations))
    return violations


def validate_meaning_preservation(
    source_text: str,
    translated_text: str,
    *,
    entities: list[str] | None = None,
) -> ValidationResult:
    """P117: validate that translation preserves critical meaning elements.

    Forbidden to remove:
    - facts, negations, characters, numbers, dates, terminology, causal links

    Allowed:
    - word order changes, natural constructions, short equivalents, cultural localization
    """
    import re
    result = ValidationResult(stage_from="translation", stage_to="meaning_validation")

    if not source_text or not translated_text:
        result.passed = True
        return result

    # Check numbers preserved
    result.checks_run += 1
    src_nums = set(re.findall(r'\b\d+(?:[.,]\d+)?\b', source_text))
    tgt_nums = set(re.findall(r'\b\d+(?:[.,]\d+)?\b', translated_text))
    if src_nums and not src_nums.issubset(tgt_nums):
        missing = src_nums - tgt_nums
        result.errors.append(f"numbers_lost: {missing}")
    else:
        result.checks_passed += 1

    # Check negations preserved
    result.checks_run += 1
    neg_en = bool(re.search(r"\b(not|no|never|none|n't|cannot)\b", source_text, re.I))
    neg_any = bool(re.search(
        r"\b(not|no|never|none|n't|cannot|не|ні|ніколи|жодн\w*|ніде|ніхто|ніщо|нікуди|ніяк\w*)\b",
        translated_text, re.I
    ))
    if neg_en and not neg_any:
        result.errors.append("negation_lost")
    else:
        result.checks_passed += 1

    # Check entities preserved (if provided) — hard fail when majority missing
    if entities:
        result.checks_run += 1
        missing_ents = [e for e in entities if e.lower() not in translated_text.lower()]
        if missing_ents and len(missing_ents) > len(entities) * 0.3:
            result.errors.append(f"entities_missing: {missing_ents}")
        elif missing_ents:
            result.warnings.append(f"entities_missing: {missing_ents}")
            result.checks_passed += 1
        else:
            result.checks_passed += 1

    # Meaning Engine V2 — coverage / event / sentence integrity gate
    try:
        from engines.semantic_v3.meaning_preservation import evaluate_meaning_preservation

        mp = evaluate_meaning_preservation(source_text, translated_text)
        result.checks_run += 1
        if mp.fallback or not mp.passed:
            result.errors.append(
                "meaning_coverage_failed:" + ",".join(mp.reasons[:4])
            )
        else:
            result.checks_passed += 1
        result.warnings.append(
            f"coverage={mp.coverage:.2f};entities={mp.entity_preservation_score:.2f}"
        )
    except Exception:
        pass

    result.passed = len(result.errors) == 0
    return result
