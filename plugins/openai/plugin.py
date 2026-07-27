"""Builtin OpenAI translation plugin."""

from plugins._llm_provider import make_llm_plugin

Plugin = make_llm_plugin(
    "openai",
    env_keys=("OPENAI_API_KEY", "VM_OPENAI_API_KEY"),
    provider_hint="openai",
)


def create_plugin():
    return Plugin()
