"""Semantic quality audit — scores and issue detection after adaptation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from engines.ai_core.entity_dictionary import EntityDictionary
from engines.ai_core.semantic_engine.context_bundle import DialogueContext
from engines.ai_core.semantic_agent.scoring import _naturalness_score
from engines.ai_core.semantic_agent.validators.context_validator import validate_context
from engines.ai_core.semantic_agent.validators.meaning_validator import validate_meaning
from engines.mt.lang_codes import normalize_lang
from engines.pipeline_language_gate import is_critical_language_mismatch

SEMANTIC_SCORE_MIN = 0.90
MAX_SEMANTIC_RETRIES = 3

_LITERAL_CALQUES_UK = re.compile(
    r"\b(?:являється|осуществляет|данный|в настоящее время|"
    r"здійснює|даний|в даний час|не\s+мог\b|ехав\b|"
    r"компанії з фільму)\b",
    re.I,
)
_ENGLISH_IN_UK = re.compile(r"\b[A-Za-z]{3,}\b")


@dataclass
class SemanticQualityMetrics:
    semantic_score: float
    naturalness_score: float
    translation_confidence: float
    context_confidence: float
    entity_accuracy: float
    issues: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    passed: bool = False
    model_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_score": round(self.semantic_score, 4),
            "naturalness_score": round(self.naturalness_score, 4),
            "translation_confidence": round(self.translation_confidence, 4),
            "context_confidence": round(self.context_confidence, 4),
            "entity_accuracy": round(self.entity_accuracy, 4),
            "issues": self.issues,
            "fixes_applied": self.fixes_applied,
            "passed": self.passed,
            "model_used": self.model_used,
        }


def _literal_translation_score(source: str, mt: str, semantic: str) -> tuple[float, list[str]]:
    """Penalize when semantic is nearly identical to raw MT calque."""
    issues: list[str] = []
    if not semantic.strip():
        return 0.0, ["empty_semantic"]

    sim_mt = SequenceMatcher(None, mt.lower(), semantic.lower()).ratio()
    sim_src = SequenceMatcher(None, source.lower(), semantic.lower()).ratio()

    if _LITERAL_CALQUES_UK.search(semantic):
        issues.append("literal_calque")
    if sim_mt > 0.97 and len(mt) > 15:
        issues.append("unchanged_mt")
    if sim_src > 0.55 and normalize_lang("uk") == "uk":
        issues.append("source_language_leak")

    score = 1.0
    if "literal_calque" in issues:
        score -= 0.25
    if "unchanged_mt" in issues:
        score -= 0.20
    if "source_language_leak" in issues:
        score -= 0.15
    return max(0.0, score), issues


def audit_semantic_output(
    *,
    source: str,
    machine_translation: str,
    semantic_text: str,
    dialogue: DialogueContext,
    entity_dict: EntityDictionary,
    target_lang: str,
    translation_confidence: float = 0.85,
    llm_used: bool = False,
    model_name: str = "",
) -> SemanticQualityMetrics:
    """Automatic quality gate after Semantic Agent."""
    tgt = normalize_lang(target_lang)
    semantic = str(semantic_text or "").strip()
    mt = str(machine_translation or "").strip()
    src = str(source or "").strip()
    issues: list[str] = []

    meaning = validate_meaning(src, mt, semantic)
    context = validate_context(
        src,
        mt,
        semantic,
        prev_context=dialogue.prev_context_text(),
    )
    naturalness = _naturalness_score(mt, semantic, tgt)
    entity_accuracy = entity_dict.accuracy(semantic, source=src)
    literal_score, literal_issues = _literal_translation_score(src, mt, semantic)
    issues.extend(literal_issues)

    if meaning.score < 0.75:
        issues.append("meaning_loss")
    if not meaning.ok:
        issues.extend(meaning.issues[:3])

    bad, code = is_critical_language_mismatch(semantic, target_lang=tgt, original=src)
    if bad:
        issues.append(code or "language_mix")

    if entity_accuracy < 0.85:
        issues.append("entity_inaccuracy")

    if naturalness < 0.70:
        issues.append("unnatural_phrasing")

    if tgt == "uk":
        for word in _ENGLISH_IN_UK.findall(semantic):
            if word.lower() in {"usc", "george", "jr", "hollywood"}:
                continue
            if len(word) > 4:
                issues.append("untranslated_word")
                break

    semantic_score = round(
        0.30 * meaning.score
        + 0.20 * naturalness
        + 0.20 * entity_accuracy
        + 0.15 * context.score
        + 0.10 * literal_score
        + 0.05 * min(1.0, translation_confidence),
        4,
    )

    passed = semantic_score >= SEMANTIC_SCORE_MIN and "meaning_loss" not in issues

    return SemanticQualityMetrics(
        semantic_score=semantic_score,
        naturalness_score=round(naturalness, 4),
        translation_confidence=round(min(1.0, translation_confidence), 4),
        context_confidence=round(context.score, 4),
        entity_accuracy=round(entity_accuracy, 4),
        issues=sorted(set(issues)),
        passed=passed,
        model_used=model_name if llm_used else "rule_engine",
    )
