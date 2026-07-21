"""Lip Sync — Cinema mode (V1 stub)."""

from __future__ import annotations

from typing import Any

from engines.streamdub.base import ModuleCapabilities, StreamModule


class LipSyncEngine(StreamModule):
    module_id = "lip_sync"

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        pass

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        return False, "planned_v2", {"status": "stub"}

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            features=["lip_sync"],
            meta={"status": "stub", "planned": True},
        )

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "lip_sync": "passthrough"}
