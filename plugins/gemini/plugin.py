"""Builtin Gemini translation plugin."""

from plugins._llm_provider import make_llm_plugin

Plugin = make_llm_plugin(
    "gemini",
    env_keys=("GOOGLE_API_KEY", "GEMINI_API_KEY", "VM_GEMINI_API_KEY"),
    provider_hint="gemini",
)


def create_plugin():
    return Plugin()
