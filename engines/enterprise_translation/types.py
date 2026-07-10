"""Enterprise translation shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    PERSON = "PERSON"
    ORG = "ORG"
    PLACE = "PLACE"
    TITLE = "TITLE"
    PRODUCT = "PRODUCT"
    COMPANY = "COMPANY"
    EVENT = "EVENT"
    DATE = "DATE"
    OTHER = "OTHER"


@dataclass
class EntityRecord:
    entity_id: str
    entity_type: EntityType
    original: str
    normalized: str = ""
    display: str = ""
    aliases: list[str] = field(default_factory=list)
    restore_variants: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "original": self.original,
            "normalized": self.normalized,
            "display": self.display,
            "aliases": self.aliases,
            "restore_variants": self.restore_variants,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EntityRecord:
        et = d.get("entity_type", "OTHER")
        try:
            entity_type = EntityType(str(et))
        except ValueError:
            entity_type = EntityType.OTHER
        return cls(
            entity_id=str(d.get("entity_id") or ""),
            entity_type=entity_type,
            original=str(d.get("original") or ""),
            normalized=str(d.get("normalized") or ""),
            display=str(d.get("display") or ""),
            aliases=list(d.get("aliases") or []),
            restore_variants=list(d.get("restore_variants") or []),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class MaskResult:
    masked_text: str
    token_map: dict[str, str]  # token -> entity_id
    registry_snapshot: list[EntityRecord]


@dataclass
class TournamentCandidate:
    engine_id: str
    text: str
    elapsed_ms: float
    score: float
    score_details: dict[str, Any] = field(default_factory=dict)
    placeholder_ok: bool = True
    error: str = ""


@dataclass
class FusionResult:
    text: str
    winner_engine: str
    winner_score: float
    candidates: list[TournamentCandidate]
    fusion_reason: str
    restored_entities: list[str] = field(default_factory=list)
