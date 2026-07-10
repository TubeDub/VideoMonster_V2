"""TubeDub LLM provider layer — model families over Ollama/OpenAI-compatible backends."""

from engines.llm_providers.base import LLMProvider
from engines.llm_providers.registry import (
    DEFAULT_FAMILY_ID,
    FALLBACK_FAMILY_ORDER,
    get_provider,
    list_providers,
    list_providers_for_ui,
    load_persisted_selection,
    resolve_model,
    resolve_provider_for_model,
    save_persisted_selection,
)

from engines.llm_providers.transport import list_cloud_profiles, resolve_transport

__all__ = [
    "LLMProvider",
    "DEFAULT_FAMILY_ID",
    "FALLBACK_FAMILY_ORDER",
    "get_provider",
    "list_providers",
    "list_providers_for_ui",
    "load_persisted_selection",
    "save_persisted_selection",
    "resolve_model",
    "resolve_provider_for_model",
    "list_cloud_profiles",
    "resolve_transport",
]
