"""P108 — Entity Graph (stable entity IDs across sentences)."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence

_NUM = re.compile(r"^\d[\d.,]*$")
_DATE = re.compile(r"^\d{1,4}[-./]\d{1,2}([-./]\d{1,4})?$")


@dataclass
class EntityNode:
    entity_id: str
    canonical: str
    entity_type: str  # PERSON|ORG|PLACE|BRAND|NUMBER|DATE|OTHER
    aliases: list[str] = field(default_factory=list)
    sentence_uuids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EntityGraph:
    nodes: dict[str, EntityNode] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": [n.to_dict() for n in self.nodes.values()]}


def _guess_type(text: str, hint: str = "") -> str:
    if hint:
        return hint
    t = text.strip()
    if _NUM.match(t):
        return "NUMBER"
    if _DATE.match(t):
        return "DATE"
    if t[:1].isupper() and len(t) > 1:
        return "PERSON"
    return "OTHER"


def build_entity_graph(sentences: list[SemanticSentence]) -> EntityGraph:
    """One Entity ID per canonical name across the project."""
    by_key: dict[str, EntityNode] = {}
    for s in sentences:
        names = list(s.entities)
        for w in s.words:
            if w.entity_type or w.entity:
                names.append(w.text.strip(".,!?;:\"'«»"))
        for name in names:
            if not name:
                continue
            key = name.lower()
            if key not in by_key:
                et = "OTHER"
                for w in s.words:
                    if w.text.strip(".,!?;:\"'«»").lower() == key:
                        et = _guess_type(name, w.entity_type or w.entity)
                        break
                else:
                    et = _guess_type(name)
                by_key[key] = EntityNode(
                    entity_id=f"ent_{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:12]}",
                    canonical=name,
                    entity_type=et,
                    aliases=[name],
                )
            node = by_key[key]
            if s.sentence_uuid not in node.sentence_uuids:
                node.sentence_uuids.append(s.sentence_uuid)
            if name not in node.aliases:
                node.aliases.append(name)
            # Stamp words
            for w in s.words:
                if w.text.strip(".,!?;:\"'«»").lower() == key:
                    w.entity_id = node.entity_id
                    w.entity_type = node.entity_type
                    w.entity = node.entity_type
        # Normalize sentence entities to canonicals
        s.entities = list(
            dict.fromkeys(
                by_key[e.lower()].canonical for e in s.entities if e.lower() in by_key
            )
        ) or s.entities
    return EntityGraph(nodes={n.entity_id: n for n in by_key.values()})
