"""Builtin stub — translation (translation)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('translation', ['translation'], version='1.0.0', description='Builtin translation')
def create_plugin():
    return Plugin()
