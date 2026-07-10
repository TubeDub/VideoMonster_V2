"""Builtin stub — edge_tts (tts)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('edge_tts', ['tts'], version='1.0.0', description='Builtin edge_tts')
def create_plugin():
    return Plugin()
