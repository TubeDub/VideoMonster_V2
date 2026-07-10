# VideoMonster V2 SDK — API Reference

## Core API Version

`1.0.0` — plugins declare `"minimum_api": "1.0.0"` in `plugin.json`.

## Plugin Interface (`VMPlugin` / `BasePlugin`)

| Method | Description |
|--------|-------------|
| `initialize(context)` | Called once on load |
| `shutdown()` | Called on disable/unload |
| `health()` | Returns `PluginHealth` |
| `capabilities()` | List of capability strings |
| `version()` | Plugin version |
| `dependencies()` | Plugin dependency names |
| `execution_mode()` | `local` / `remote` / `hybrid` (§17) |
| `remote_endpoint()` | Optional remote URL |

## SDK Registration (`sdk.core_api`)

```python
from sdk.core_api import (
    register_plugin,
    register_agent,
    register_model,
    register_exporter,
    register_tts,
    register_stt,
    register_translation,
    register_review,
    register_event,
    register_memory_provider,
)
```

## Capabilities

`translation`, `tts`, `stt`, `voice_clone`, `lip_sync`, `subtitle`, `export`,
`review`, `ocr`, `noise_reduction`, `mix`, `timing`, `memory`, `utility`

## Plugin Manager

```python
from core.plugin_manager import get_plugin_manager

mgr = get_plugin_manager()
mgr.discover()
mgr.enable("my_plugin")
mgr.disable("my_plugin")
mgr.reload("my_plugin")
mgr.list_plugins()
mgr.plugins_for_capability("translation")
```

## Marketplace API (stub)

```python
mgr.marketplace.install("/path/to/plugin")
mgr.marketplace.update("name", "/path/to/new")
mgr.marketplace.remove("name")
mgr.marketplace.enable("name")
mgr.marketplace.disable("name")
```
