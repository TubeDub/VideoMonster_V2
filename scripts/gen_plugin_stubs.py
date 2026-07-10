"""One-time generator for builtin plugin stubs."""
import json
from pathlib import Path

PLUGINS = [
    ("whisper", "stt", {"gpu": True, "audio": True}),
    ("ollama", "translation", {"network": True, "gpu": True}),
    ("openai", "translation", {"network": True}),
    ("claude", "translation", {"network": True}),
    ("gemini", "translation", {"network": True}),
    ("deepseek", "translation", {"network": True}),
    ("edge_tts", "tts", {"network": True, "audio": True}),
    ("elevenlabs", "tts", {"network": True, "audio": True}),
    ("voice_clone", "voice_clone", {"gpu": True, "audio": True}),
    ("lip_sync", "lip_sync", {"gpu": True, "video": True}),
    ("subtitle", "subtitle", {"file": True, "video": True}),
    ("translation", "translation", {"network": True}),
]

root = Path(__file__).parent.parent / "plugins"
for name, cap, perms in PLUGINS:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    perms_full = {k: perms.get(k, False) for k in ("file", "network", "gpu", "audio", "video", "memory")}
    manifest = {
        "name": name,
        "version": "1.0.0",
        "author": "VideoMonster",
        "description": f"Builtin {name} capability provider",
        "minimum_api": "1.0.0",
        "dependencies": [],
        "capabilities": [cap],
        "permissions": perms_full,
        "entry_point": "plugin.py",
        "execution_mode": "local",
    }
    (d / "plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    py = (
        f'"""Builtin stub — {name} ({cap})."""\n'
        f"from sdk.stub import stub_plugin\n"
        f"Plugin = stub_plugin('{name}', ['{cap}'], version='1.0.0', description='Builtin {name}')\n"
        f"def create_plugin():\n"
        f"    return Plugin()\n"
    )
    (d / "plugin.py").write_text(py, encoding="utf-8")
print("ok", len(PLUGINS))
