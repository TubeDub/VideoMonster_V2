"""P116 — Semantic Lock Preparation (pre-lock immutable facts)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence

_NUM = re.compile(r"\d[\d.,]*")
_DATE = re.compile(
    r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})\b",
    re.I,
)


@dataclass
class LockPreparation:
    sentence_uuid: str
    immutable_entities: list[str] = field(default_factory=list)
    immutable_facts: list[str] = field(default_factory=list)
    immutable_numbers: list[str] = field(default_factory=list)
    immutable_dates: list[str] = field(default_factory=list)
    immutable_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_semantic_lock(sentences: list[SemanticSentence]) -> list[LockPreparation]:
    """
    Do NOT set Semantic Lock yet.
    Pre-compute what must survive Translation unchanged.
    """
    out: list[LockPreparation] = []
    for s in sentences:
        names = [
            e
            for e in s.entities
            if any(
                (w.entity_type or w.entity) in ("PERSON", "ORG", "PLACE", "BRAND")
                or (w.text.strip(".,!?").lower() == e.lower())
                for w in s.words
            )
        ] or list(s.entities)
        numbers = _NUM.findall(s.text)
        dates = [m.group(0) for m in _DATE.finditer(s.text)]
        facts = []
        if s.subjects and s.verbs:
            facts.append(f"{s.subjects[0]}::{s.verbs[0]}")
        prep = LockPreparation(
            sentence_uuid=s.sentence_uuid,
            immutable_entities=list(dict.fromkeys(names)),
            immutable_facts=facts,
            immutable_numbers=list(dict.fromkeys(numbers)),
            immutable_dates=list(dict.fromkeys(dates)),
            immutable_names=list(dict.fromkeys(names)),
        )
        s.locked_entities = list(prep.immutable_entities)
        s.locked_numbers = list(
            dict.fromkeys([*prep.immutable_numbers, *prep.immutable_dates])
        )
        s.lock_status = "prepared"
        s.context = {
            **(s.context or {}),
            "lock_preparation": prep.to_dict(),
        }
        out.append(prep)
    return out
