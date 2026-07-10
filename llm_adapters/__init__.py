"""LLM adapters (TZ #3 §4).

Adapter registry: maps ``ModelDescriptor.adapter`` id → adapter class. Adding a
new backend means adding a module here and registering its class — the Dispatcher
and the rest of the program never change.
"""

from __future__ import annotations

from typing import Any

from llm_adapters.anthropic import AnthropicAdapter
from llm_adapters.base import ChatRequest, ChatResult, HealthReport, LLMAdapter
from llm_adapters.gemini import GeminiAdapter
from llm_adapters.openai_compatible import OpenAICompatibleAdapter

_ADAPTERS: dict[str, type[LLMAdapter]] = {
    OpenAICompatibleAdapter.adapter_id: OpenAICompatibleAdapter,
    AnthropicAdapter.adapter_id: AnthropicAdapter,
    GeminiAdapter.adapter_id: GeminiAdapter,
}


def register_adapter(adapter_cls: type[LLMAdapter]) -> None:
    _ADAPTERS[adapter_cls.adapter_id] = adapter_cls


def get_adapter_class(adapter_id: str) -> type[LLMAdapter]:
    return _ADAPTERS.get(adapter_id, OpenAICompatibleAdapter)


def build_adapter(descriptor: Any) -> LLMAdapter:
    cls = get_adapter_class(getattr(descriptor, "adapter", "openai_compatible"))
    return cls(descriptor)


__all__ = [
    "ChatRequest",
    "ChatResult",
    "HealthReport",
    "LLMAdapter",
    "OpenAICompatibleAdapter",
    "AnthropicAdapter",
    "GeminiAdapter",
    "register_adapter",
    "get_adapter_class",
    "build_adapter",
]
