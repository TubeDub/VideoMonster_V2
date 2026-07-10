"""Meta Llama model family."""

from __future__ import annotations

import re

from engines.llm_providers.base import LLMProvider

_LLAMA_RE = re.compile(r"llama", re.I)


class LlamaProvider(LLMProvider):
    family_id = "llama"
    display_name = "Llama"
    default_model = "llama3.1:8b"
    candidate_models = (
        "llama3.1:8b",
        "llama3.2:3b",
        "llama3.2:1b",
        "llama3:8b",
        "llama3:70b",
    )

    def matches_installed(self, model_name: str) -> bool:
        return bool(_LLAMA_RE.search(str(model_name or "")))


PROVIDER = LlamaProvider(
    family_id="llama",
    display_name="Llama",
    default_model="llama3.1:8b",
    candidate_models=LlamaProvider.candidate_models,
)
