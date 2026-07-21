"""P102 — Word Graph (subject/verb/object, pronoun→entity, negation→verb)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence, SemanticWord

_VERBS = re.compile(
    r"(?i)^(is|are|was|were|be|have|has|had|do|does|did|go|went|make|made|"
    r"say|said|get|got|know|drive|drove|see|saw|want|need|come|came|"
    r"є|був|була|має|сказати|їхати|бачити|хотіти)$"
)
_PRONOUNS = {
    "he", "she", "it", "they", "him", "her", "them", "his", "their",
    "він", "вона", "воно", "вони", "його", "її", "їх",
}
_NEGATION = {"not", "no", "never", "n't", "не", "ні", "ніколи"}


@dataclass
class WordEdge:
    source_uuid: str
    target_uuid: str
    relation: str  # subject|verb|object|pronoun_ref|negation|dep

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WordGraph:
    edges: list[WordEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"edges": [e.to_dict() for e in self.edges], "count": len(self.edges)}


def build_word_graph(sentences: list[SemanticSentence]) -> WordGraph:
    edges: list[WordEdge] = []
    last_entity_uuid = ""

    for s in sentences:
        words = s.words
        verb_idx = next(
            (i for i, w in enumerate(words) if _VERBS.match(w.normalized_text or w.text)),
            -1,
        )
        subject = words[0] if words else None
        obj = None
        if verb_idx >= 0 and verb_idx + 1 < len(words):
            obj = words[verb_idx + 1]
        if subject and verb_idx >= 0:
            verb = words[verb_idx]
            edges.append(WordEdge(subject.word_uuid, verb.word_uuid, "subject"))
            subject.dependency_parent = verb.word_uuid
            subject.dependency = "subject"
            verb.dependency = "root"
            if obj:
                edges.append(WordEdge(verb.word_uuid, obj.word_uuid, "object"))
                obj.dependency_parent = verb.word_uuid
                obj.dependency = "object"
                verb.dependency_children = list(
                    dict.fromkeys([*(verb.dependency_children or []), obj.word_uuid])
                )

        for w in words:
            w.sentence_uuid = s.sentence_uuid
            norm = (w.normalized_text or w.text).lower()
            if norm in _PRONOUNS and last_entity_uuid:
                edges.append(WordEdge(w.word_uuid, last_entity_uuid, "pronoun_ref"))
                w.dependency = "pronoun_ref"
                w.dependency_parent = last_entity_uuid
            if norm in _NEGATION and verb_idx >= 0:
                edges.append(
                    WordEdge(w.word_uuid, words[verb_idx].word_uuid, "negation")
                )
            if w.entity_type or w.entity:
                last_entity_uuid = w.word_uuid

    return WordGraph(edges=edges)
