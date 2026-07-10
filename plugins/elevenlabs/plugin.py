"""Builtin stub — elevenlabs (tts)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('elevenlabs', ['tts'], version='1.0.0', description='Builtin elevenlabs')
def create_plugin():
    return Plugin()
