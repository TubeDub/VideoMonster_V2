"""Builtin Claude translation plugin."""

from plugins._llm_provider import make_llm_plugin

Plugin = make_llm_plugin(
    "claude",
    env_keys=("ANTHROPIC_API_KEY", "VM_ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    provider_hint="anthropic",
)


def create_plugin():
    return Plugin()
