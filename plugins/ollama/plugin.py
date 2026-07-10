"""Builtin stub — ollama (translation)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('ollama', ['translation'], version='1.0.0', description='Builtin ollama')
def create_plugin():
    return Plugin()
