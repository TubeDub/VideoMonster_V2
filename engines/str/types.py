"""Unified STR translation result — same shape for every engine plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class STRTranslationResult:
    """Uniform output every MT adapter must produce."""

    text: str
    engine_id: str
    src_lang: str
    tgt_lang: str
    elapsed_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)
    quality_probability: float = 0.0
    error: str = ""
    offline: bool = True
    engine_version: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and bool(str(self.text or "").strip())

    def to_meta(self) -> dict[str, Any]:
        return {
            "engine": self.engine_id,
            "engine_version": self.engine_version,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "warnings": list(self.warnings),
            "quality_probability": round(self.quality_probability, 2),
            "error": self.error,
            "offline": self.offline,
            **self.meta,
        }
