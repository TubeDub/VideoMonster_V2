"""P115 — Semantic Validator (pre-Translation gate)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence


@dataclass
class ValidationIssue:
    sentence_uuid: str
    code: str
    message: str
    severity: str = "warn"  # warn|error

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    reanalyze: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "reanalyze": list(self.reanalyze),
        }


def validate_semantic_sentences(
    sentences: list[SemanticSentence],
    *,
    min_confidence: float = 0.70,
) -> SemanticValidationReport:
    """
    Detect lost words, broken thoughts, bad boundaries, bad entities, broken deps.
    Low-quality sentences are marked for return to Sentence Builder.
    """
    issues: list[ValidationIssue] = []
    reanalyze: list[str] = []

    for s in sentences:
        if not (s.text or "").strip():
            issues.append(
                ValidationIssue(s.sentence_uuid, "empty_text", "empty sentence", "error")
            )
            reanalyze.append(s.sentence_uuid)
            continue
        # Lost words: text token count vs word objects
        tokens = [t for t in s.text.split() if t.strip(".,!?;:")]
        if s.words and abs(len(tokens) - len(s.words)) > max(2, len(s.words) // 3):
            issues.append(
                ValidationIssue(
                    s.sentence_uuid,
                    "lost_words",
                    f"token/word mismatch {len(tokens)} vs {len(s.words)}",
                    "error",
                )
            )
            reanalyze.append(s.sentence_uuid)
        if s.is_incomplete or s.sentence_type == "incomplete":
            issues.append(
                ValidationIssue(
                    s.sentence_uuid, "broken_thought", "incomplete thought", "error"
                )
            )
            reanalyze.append(s.sentence_uuid)
        if s.sentence_confidence < min_confidence:
            issues.append(
                ValidationIssue(
                    s.sentence_uuid,
                    "low_confidence",
                    f"confidence {s.sentence_confidence:.2f} < {min_confidence}",
                    "error",
                )
            )
            reanalyze.append(s.sentence_uuid)
        # Bad entities: empty entity_type with capitalized mid-sentence token
        for w in s.words[1:]:
            bare = w.text.strip(".,!?")
            if bare[:1].isupper() and len(bare) > 1 and not (w.entity_type or w.entity):
                issues.append(
                    ValidationIssue(
                        s.sentence_uuid,
                        "entity_miss",
                        f"possible entity without type: {bare}",
                        "warn",
                    )
                )
        # Broken dependencies: claimed parent missing
        uuids = {w.word_uuid for w in s.words}
        for w in s.words:
            if w.dependency_parent and w.dependency_parent not in uuids:
                issues.append(
                    ValidationIssue(
                        s.sentence_uuid,
                        "broken_dependency",
                        f"missing parent for {w.text}",
                        "error",
                    )
                )
                reanalyze.append(s.sentence_uuid)

        if s.sentence_uuid in reanalyze:
            s.semantic_status = "needs_review"
            s.recovery_plan = list(
                dict.fromkeys([*(s.recovery_plan or []), "return_to_sentence_builder"])
            )
        else:
            s.semantic_status = "validated"

    # Deduplicate reanalyze
    reanalyze = list(dict.fromkeys(reanalyze))
    errors = [i for i in issues if i.severity == "error"]
    return SemanticValidationReport(
        ok=len(errors) == 0, issues=issues, reanalyze=reanalyze
    )
