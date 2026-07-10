# SDK Examples

## Demo Plugin

`plugins/demo/plugin.py` — registers a translation handler.

## Hello Plugin

```python
from sdk.base import BasePlugin

class Plugin(BasePlugin):
    PLUGIN_NAME = "hello"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["utility"]

def create_plugin():
    return Plugin()
```

## Register Translation

```python
from sdk.core_api import register_translation

def my_translate(text, **kw):
    return text.upper()

register_translation("uppercase", my_translate, plugin_name="hello")
```

## Hot Reload

```
POST /api/plugins/hello/reload
```

No application restart required.
