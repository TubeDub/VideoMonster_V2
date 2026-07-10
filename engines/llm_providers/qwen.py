"""Alibaba Qwen model family."""

from __future__ import annotations

import re

from engines.llm_providers.base import LLMProvider

_QWEN_RE = re.compile(r"qwen", re.I)


class QwenProvider(LLMProvider):
    family_id = "qwen"
    display_name = "Qwen"
    default_model = "qwen2.5:7b"
    candidate_models = (
        "qwen2.5:7b",
        "qwen2.5:3b",
        "qwen2.5:14b",
        "qwen2.5:1.5b",
        "qwen3:8b",
    )

    def matches_installed(self, model_name: str) -> bool:
        return bool(_QWEN_RE.search(str(model_name or "")))


PROVIDER = QwenProvider(
    family_id="qwen",
    display_name="Qwen",
    default_model="qwen2.5:7b",
    candidate_models=QwenProvider.candidate_models,
)
