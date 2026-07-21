"""P110 — Conversation Memory (project-scoped; clear on project end)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence

_PRONOUNS = {
    "he", "she", "it", "they", "him", "her", "them",
    "він", "вона", "воно", "вони", "його", "її", "їх",
}


@dataclass
class ConversationMemory:
    project_uuid: str
    names: list[str] = field(default_factory=list)
    pronouns: list[str] = field(default_factory=list)
    terminology: list[str] = field(default_factory=list)
    speaking_style: str = ""
    character_relations: list[str] = field(default_factory=list)
    open_topics: list[str] = field(default_factory=list)
    active_entities: list[str] = field(default_factory=list)
    cleared: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def clear(self) -> None:
        """P110 — fully clear after project completion."""
        self.names.clear()
        self.pronouns.clear()
        self.terminology.clear()
        self.speaking_style = ""
        self.character_relations.clear()
        self.open_topics.clear()
        self.active_entities.clear()
        self.cleared = True


def build_conversation_memory(
    sentences: list[SemanticSentence],
    *,
    project_uuid: str,
    terminology: list[str] | None = None,
) -> ConversationMemory:
    mem = ConversationMemory(project_uuid=project_uuid)
    mem.terminology = list(terminology or [])
    for s in sentences:
        for e in s.entities:
            if e not in mem.names:
                mem.names.append(e)
            if e not in mem.active_entities:
                mem.active_entities.append(e)
        mem.active_entities = mem.active_entities[-16:]
        for w in s.text.split():
            wl = w.lower().strip(".,!?")
            if wl in _PRONOUNS and wl not in mem.pronouns:
                mem.pronouns.append(wl)
        if s.is_incomplete or s.sentence_type == "incomplete":
            mem.open_topics.append(s.text[:80])
        if s.style:
            mem.speaking_style = s.style
        if len(s.entities) >= 2:
            rel = f"{s.entities[0]}↔{s.entities[1]}"
            if rel not in mem.character_relations:
                mem.character_relations.append(rel)
        # Attach memory snapshot onto sentence context (read-only view)
        s.context = {
            **(s.context or {}),
            "memory_names": list(mem.names[-8:]),
            "memory_pronouns": list(mem.pronouns[-8:]),
            "memory_active_entities": list(mem.active_entities),
            "open_topics": list(mem.open_topics[-4:]),
        }
    return mem
