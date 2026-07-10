"""DeepSeek model family (default TubeDub local AI)."""

from __future__ import annotations

import re

from engines.llm_providers.base import LLMProvider

_DEEPSEEK_RE = re.compile(r"deepseek", re.I)


class DeepSeekProvider(LLMProvider):
    family_id = "deepseek"
    display_name = "DeepSeek"
    default_model = "deepseek-r1:7b"
    candidate_models = (
        "deepseek-r1:7b",
        "deepseek-r1:8b",
        "deepseek-r1:14b",
        "deepseek-r1:1.5b",
        "deepseek-v3",
        "deepseek-v2",
    )

    def matches_installed(self, model_name: str) -> bool:
        return bool(_DEEPSEEK_RE.search(str(model_name or "")))


PROVIDER = DeepSeekProvider(
    family_id="deepseek",
    display_name="DeepSeek",
    default_model="deepseek-r1:7b",
    candidate_models=DeepSeekProvider.candidate_models,
)
