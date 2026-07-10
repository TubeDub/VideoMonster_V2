"""Builtin stub — claude (translation)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('claude', ['translation'], version='1.0.0', description='Builtin claude')
def create_plugin():
    return Plugin()
