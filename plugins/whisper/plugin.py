"""Builtin stub — whisper (stt)."""
from sdk.stub import stub_plugin
Plugin = stub_plugin('whisper', ['stt'], version='1.0.0', description='Builtin whisper')
def create_plugin():
    return Plugin()
