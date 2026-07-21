"""Identity backend — deterministic passthrough (tests / offline)."""

from __future__ import annotations

from typing import Any

from engines.translation_core.backend import BackendCapabilities, TranslationBackend


class IdentityBackend(TranslationBackend):
    id = "identity"
    name = "Identity"
    version = "1"

    def initialize(self) -> None:
        return None

    def translate(
        self,
        text: str,
        *,
        src_lang: str,
        tgt_lang: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        return str(text or "")

    def health_check(self) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(offline=True, multi_variant=False, context_aware=True)
