"""Builtin DeepSeek translation plugin."""

from plugins._llm_provider import make_llm_plugin

Plugin = make_llm_plugin(
    "deepseek",
    env_keys=("DEEPSEEK_API_KEY", "VM_DEEPSEEK_API_KEY"),
    provider_hint="deepseek",
)


def create_plugin():
    return Plugin()
