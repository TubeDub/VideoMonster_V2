# Plugin Development Guide

## Quick Start

1. Copy `sdk/template/` to `plugins/my_plugin/`
2. Edit `plugin.json` — set name, capabilities, permissions
3. Implement `Plugin` class extending `sdk.base.BasePlugin`
4. Restart or call `POST /api/plugins/my_plugin/reload`

## Manifest (`plugin.json`)

Required fields: `name`, `version`, `minimum_api`, `capabilities`.

## Permissions

Declare what your plugin needs. Users can restrict via
`POST /api/plugins/{name}/permissions`.

## Rules

- Never import or modify core pipeline modules
- Use `sdk.core_api` for all registrations
- Fail gracefully — errors must not crash other plugins
- Target `minimum_api: "1.0.0"`

## Testing

```bash
python -m pytest tests/test_plugins.py -q
```

See `plugins/demo/` for a working reference plugin.
