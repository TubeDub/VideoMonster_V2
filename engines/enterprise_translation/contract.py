"""Placeholder Contract — invariant checks after each stage."""

from __future__ import annotations

import re

from engines.enterprise_translation.config import strict_contract
from engines.enterprise_translation.exceptions import IntegrityException
from engines.enterprise_translation.registry import PlaceholderRegistry
from engines.enterprise_translation.serializer import EntitySerializer

# Damaged / leaked placeholder patterns
_DAMAGE_RE = re.compile(
    r"(?:PERSON|ORG|PLACE|TITLE|PRODUCT|COMPANY|EVENT|DATE)[_\s]?\d+",
    re.IGNORECASE,
)
_MERGED_RE = re.compile(r"\[\[[^\]]+\]\]\[\[|\{\{|\(\(|\<\<")


class PlaceholderContract:
    def __init__(
        self,
        registry: PlaceholderRegistry,
        serializer: EntitySerializer,
        engine_id: str,
    ):
        self.registry = registry
        self.serializer = serializer
        self.engine_id = engine_id

    def expected_count(self) -> int:
        return len(self.registry.all_records())

    def _damage_fragments(self, text: str) -> list[str]:
        """Find corrupted placeholder fragments not part of valid engine tokens."""
        working = str(text or "")
        pat = self.serializer.token_pattern(self.engine_id)
        working = pat.sub("", working)
        return _DAMAGE_RE.findall(working)

    def find_tokens_in_text(self, text: str) -> list[str]:
        pat = self.serializer.token_pattern(self.engine_id)
        return pat.findall(str(text or ""))

    def verify_after_stage(
        self,
        text: str,
        *,
        stage: str,
        expected_tokens: list[str] | None = None,
        allow_no_tokens: bool = False,
    ) -> dict:
        """
        Verify placeholder invariants. Raises IntegrityException if strict.
        Returns diagnostics dict.
        """
        text = str(text or "")
        found = self.find_tokens_in_text(text)
        expected = expected_tokens if expected_tokens is not None else [
            self.serializer.get_token_for_engine(r.entity_id, self.engine_id)
            for r in self.registry.all_records()
        ]
        damages = self._damage_fragments(text)
        merged = bool(_MERGED_RE.search(text))

        missing = [t for t in expected if t not in text and not allow_no_tokens]
        extra = [t for t in found if t not in expected]

        ok = (
            not damages
            and not merged
            and (allow_no_tokens or len(missing) == 0)
            and len(extra) == 0
        )

        diag = {
            "stage": stage,
            "expected_count": len(expected),
            "found_count": len(found),
            "missing": missing,
            "extra": extra,
            "damages": damages,
            "merged": merged,
            "ok": ok,
        }

        if not ok and strict_contract():
            raise IntegrityException(
                f"Placeholder contract failed at {stage}",
                stage=stage,
                details=diag,
            )
        return diag
