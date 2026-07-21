"""P216 Semantic Lock for Translation Core + P208/P209 context helpers."""

from __future__ import annotations

from typing import Any

from engines.semantic_v3.semantic_lock import apply_locked_translation, lock_sentence
from engines.semantic_v3.types import SemanticSentence
from engines.translation_core.terminology import extract_protected_tokens


def build_translation_context(
    sentences: list[SemanticSentence],
    index: int,
) -> dict[str, Any]:
    """P208 Context Translation payload (prev/next/dialogue/emotion/style/scene)."""
    s = sentences[index]
    prev_t = sentences[index - 1].text if index > 0 else ""
    next_t = sentences[index + 1].text if index + 1 < len(sentences) else ""
    return {
        "prev": prev_t,
        "next": next_t,
        "dialogue_id": getattr(s, "dialogue_id", "") or "",
        "emotion": s.emotion or "neutral",
        "style": getattr(s, "style", "") or "",
        "scene_uuid": getattr(s, "scene_uuid", "") or "",
        "entities": list(s.entities or []),
        "context": dict(getattr(s, "context", None) or {}),
    }


def apply_style_hint(text: str, style: str, emotion: str) -> str:
    """P209 — soft style marker for backends that read context (non-mutating core)."""
    # Context is passed separately; keep text clean for Entity Preservation
    return text


def lock_translated_sentence(sent: SemanticSentence, translated: str) -> SemanticSentence:
    """P216 — establish Semantic Lock after validation."""
    # Ensure protected facts from source are recorded
    protected = extract_protected_tokens(sent.text, list(sent.entities or []))
    sent.locked_entities = list(dict.fromkeys([*(sent.entities or []), *protected]))
    apply_locked_translation(sent, translated)
    sent.lock_status = "locked"
    sent.translation_status = "locked"
    sent.semantic_locked = True
    return lock_sentence(sent)


def assert_post_lock_immutable(sent: SemanticSentence, new_text: str) -> None:
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    if not sent.semantic_locked:
        return
    if (new_text or "").strip() != (sent.translated_text or "").strip():
        raise ArchitectureViolation(
            "P216: automatic meaning/text change after Semantic Lock forbidden",
            stage="translation_core",
            rule="semantic_lock",
            segment_id=sent.sentence_uuid,
        )
