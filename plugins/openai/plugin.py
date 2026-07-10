"""Builtin stub — openai (translation)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('openai', ['translation'], version='1.0.0', description='Builtin openai')
def create_plugin():
    return Plugin()
