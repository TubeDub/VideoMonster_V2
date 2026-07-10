"""EntityManager — sole authority for entity masking and restoration."""

from __future__ import annotations

import re
from pathlib import Path

from engines.enterprise_translation.registry import PlaceholderRegistry
from engines.enterprise_translation.serializer import EntitySerializer
from engines.enterprise_translation.types import EntityType, MaskResult
from engines.enterprise_translation.fuzzy_restore import fuzzy_restore_tokens
from engines.enterprise_translation.contract import PlaceholderContract

# NER label → EntityType
_NER_MAP = {
    "PERSON": EntityType.PERSON,
    "PER": EntityType.PERSON,
    "ORG": EntityType.ORG,
    "ORGANIZATION": EntityType.ORG,
    "GPE": EntityType.PLACE,
    "LOC": EntityType.PLACE,
    "PLACE": EntityType.PLACE,
    "WORK_OF_ART": EntityType.TITLE,
    "TITLE": EntityType.TITLE,
    "PRODUCT": EntityType.PRODUCT,
    "COMPANY": EntityType.COMPANY,
    "EVENT": EntityType.EVENT,
    "DATE": EntityType.DATE,
}


class EntityManager:
    """Only module allowed to create/modify/restore placeholders."""

    def __init__(self, app_dir: Path | None = None, engine_id: str = "default"):
        self.registry = PlaceholderRegistry(app_dir)
        self.serializer = EntitySerializer(app_dir)
        self.engine_id = engine_id
        self._token_map: dict[str, str] = {}

    def register_from_ner(
        self,
        entities: list[dict],
        *,
        display_map: dict[str, str] | None = None,
    ) -> None:
        """Register entities from NER output [{text, label}, ...]."""
        display_map = display_map or {}
        for ent in entities:
            text = str(ent.get("text") or ent.get("word") or "").strip()
            if not text or len(text) < 2:
                continue
            label = str(ent.get("label") or ent.get("entity") or "OTHER").upper()
            etype = _NER_MAP.get(label, EntityType.OTHER)
            disp = display_map.get(text, "")
            self.registry.register(text, etype, display=disp, aliases=[text])

    def mask_text(self, text: str, engine_id: str | None = None) -> MaskResult:
        """Replace known entities with engine-specific placeholder tokens."""
        eid = engine_id or self.engine_id
        masked = str(text or "")
        token_map: dict[str, str] = {}
        # longest-first to avoid partial overlap
        records = sorted(self.registry.all_records(), key=lambda r: len(r.original), reverse=True)
        for rec in records:
            if rec.original not in masked:
                continue
            token = self.serializer.get_token_for_engine(rec.entity_id, eid)
            token_map[token] = rec.entity_id
            masked = masked.replace(rec.original, token, 1)
        self._token_map = token_map
        return MaskResult(
            masked_text=masked,
            token_map=token_map,
            registry_snapshot=self.registry.all_records(),
        )

    def restore_text(
        self,
        text: str,
        *,
        engine_id: str | None = None,
        stage: str = "restore",
    ) -> tuple[str, list[str], list[str]]:
        """
        Restore placeholders to display/original values.
        Returns (restored_text, restored_ids, warnings).
        """
        eid = engine_id or self.engine_id
        working = str(text or "")
        restored_ids: list[str] = []
        warnings: list[str] = []

        # Exact deserialize tokens first
        for token, entity_id in self._token_map.items():
            rec = self.registry.get(entity_id)
            if not rec:
                continue
            if token in working:
                working = working.replace(token, rec.display or rec.original)
                restored_ids.append(entity_id)

        # Fuzzy restore damaged tokens (serializer tokens + corrupted patterns)
        remaining = PlaceholderContract(self.registry, self.serializer, eid).find_tokens_in_text(working)
        for damaged in remaining:
            fixed_id, _ = fuzzy_restore_tokens(
                damaged,
                self.registry,
                engine_id=eid,
                serializer=self.serializer,
            )
            if fixed_id:
                rec = self.registry.get(fixed_id)
                if rec:
                    working = working.replace(damaged, rec.display or rec.original)
                    restored_ids.append(fixed_id)
                    warnings.append(f"fuzzy_restore:{damaged}->{fixed_id}")
            else:
                warnings.append(f"unrestored:{damaged}")

        from engines.enterprise_translation.fuzzy_restore import scan_and_fuzzy_restore

        working, scan_notes = scan_and_fuzzy_restore(working, self.registry, engine_id=eid)
        warnings.extend(scan_notes)

        contract = PlaceholderContract(self.registry, self.serializer, eid)
        contract.verify_after_stage(working, stage=stage, allow_no_tokens=True)
        return working, restored_ids, warnings

    def serialize_for_engine(self, text: str, engine_id: str) -> str:
        mr = self.mask_text(text, engine_id)
        return mr.masked_text

    def deserialize_from_engine(self, text: str, engine_id: str) -> str:
        restored, _, _ = self.restore_text(text, engine_id=engine_id)
        return restored
