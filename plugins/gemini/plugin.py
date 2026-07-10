"""Builtin stub — gemini (translation)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('gemini', ['translation'], version='1.0.0', description='Builtin gemini')
def create_plugin():
    return Plugin()
