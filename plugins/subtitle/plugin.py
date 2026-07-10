"""Builtin stub — subtitle (subtitle)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('subtitle', ['subtitle'], version='1.0.0', description='Builtin subtitle')
def create_plugin():
    return Plugin()
