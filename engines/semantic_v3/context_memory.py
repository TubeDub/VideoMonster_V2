"""P34 — Sentence Context Memory."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence


@dataclass
class SentenceContext:
    sentence_uuid: str
    prev_uuid: str = ""
    next_uuid: str = ""
    dialogue_id: str = ""
    speaker: str = ""
    emotion: str = "neutral"
    place: str = ""
    active_entities: list[str] = field(default_factory=list)
    terminology: list[str] = field(default_factory=list)
    prior_pronouns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_context_memory(
    sentences: list[SemanticSentence],
    *,
    place: str = "",
    terminology: list[str] | None = None,
) -> list[SemanticSentence]:
    """Attach non-isolated context to every sentence."""
    terms = list(terminology or [])
    dialogue_id = ""
    active: list[str] = []
    pronouns: list[str] = []

    for i, s in enumerate(sentences):
        if s.is_dialogue or s.is_direct_speech:
            if not dialogue_id:
                dialogue_id = f"dlg_{s.sentence_uuid[:8]}"
        else:
            dialogue_id = ""

        for e in s.entities:
            if e not in active:
                active.append(e)
        active = active[-12:]

        ctx = SentenceContext(
            sentence_uuid=s.sentence_uuid,
            prev_uuid=sentences[i - 1].sentence_uuid if i > 0 else "",
            next_uuid=sentences[i + 1].sentence_uuid if i + 1 < len(sentences) else "",
            dialogue_id=dialogue_id,
            speaker=s.speaker,
            emotion=s.emotion,
            place=place,
            active_entities=list(active),
            terminology=list(terms),
            prior_pronouns=list(pronouns[-8:]),
        )
        # Store on relations channel (deterministic, no silent text mutation)
        s.context_links = [x for x in (ctx.prev_uuid, ctx.next_uuid) if x]
        s.relations = [
            r for r in s.relations if not str(r).startswith("ctx:")
        ] + [f"ctx:{k}={v}" for k, v in ctx.to_dict().items() if k != "sentence_uuid" and v]

        # Track pronouns in source for next sentences
        for w in s.text.split():
            wl = w.lower().strip(".,!?")
            if wl in {
                "he", "she", "it", "they", "him", "her", "them",
                "він", "вона", "воно", "вони", "його", "її", "їх",
            }:
                pronouns.append(wl)

        # Attach as attribute for native TE consumers
        setattr(s, "context_memory", ctx)

    return sentences
