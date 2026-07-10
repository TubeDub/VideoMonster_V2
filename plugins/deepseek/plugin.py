"""Builtin stub — deepseek (translation)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('deepseek', ['translation'], version='1.0.0', description='Builtin deepseek')
def create_plugin():
    return Plugin()
