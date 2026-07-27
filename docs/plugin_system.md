# Developer SDK + Plugin System — Stage 9 (TZ #9)

## Goal

Open modular architecture: new models, engines, agents, and services plug in
**without changing the core**.

**Principle:** The core knows only interfaces. All extensions are plugins.

## Modules

| Module | Role |
|--------|------|
| `core/plugin_api.py` | Interfaces, manifest, capabilities, versioning |
| `core/plugin_manager.py` | Discovery, load, sandbox, hot-reload, marketplace stub |
| `sdk/` | Developer SDK — base classes, templates, docs, examples |
| `plugins/` | Plugin registry directory |
| `api/plugins_api.py` | HTTP management API |

## Plugin Structure (§3–§4)

```
plugins/my_plugin/
  plugin.json    ← manifest
  plugin.py      ← Plugin class or create_plugin()
```

## Plugin API (§2)

Every plugin implements: `initialize()`, `shutdown()`, `health()`,
`capabilities()`, `version()`, `dependencies()`.

Use `sdk.base.BasePlugin` for convenience.

## Capability System (§5)

Plugins declare capabilities (`translation`, `tts`, `stt`, …).
The orchestrator resolves providers by capability — not by plugin name.

## SDK Registration (§11)

```python
from sdk.core_api import register_translation, register_tts, register_stt
```

Available: `register_plugin`, `register_agent`, `register_model`,
`register_exporter`, `register_tts`, `register_stt`, `register_translation`,
`register_review`, `register_event`, `register_memory_provider`.

## Safety (§6–§8)

- **Dependency resolver** — API version, plugin deps, Python packages
- **Sandbox** — plugin errors isolated; core continues
- **Hot reload** — `POST /api/plugins/{name}/reload`
- **Permissions** — file/network/gpu/audio/video/memory (§13)

## Marketplace API (§9)

Local package manager (default) + optional remote storefront.

`POST /api/plugins/marketplace/install|update|remove|enable|disable|install_remote`
`GET /api/plugins/marketplace/catalog|remote`

Remote requires `VM_PLUGIN_MARKETPLACE_URL` (or `VM_PLUGIN_CATALOG_URL`); otherwise
remote install/catalog hard-gates with `remote_marketplace_not_configured`.

## Monitoring Integration (§14)

`GET /api/plugins/diagnostics` — consumed by dev monitoring dashboard.

## Distributed Processing (§17)

Manifest fields `execution_mode` and `remote_endpoint` prepared for future
remote LLM/TTS/render nodes.

## HTTP Endpoints

```
GET  /api/plugins/status
GET  /api/plugins/diagnostics
GET  /api/plugins/capabilities
GET  /api/plugins/{name}
POST /api/plugins/{name}/enable|disable|reload|permissions
POST /api/plugins/marketplace/{action}
```

## UI

- `/plugins` — plugin management page

## SDK Documentation

- `sdk/docs/API_REFERENCE.md`
- `sdk/docs/ARCHITECTURE.md`
- `sdk/docs/PLUGIN_GUIDE.md`
- `sdk/docs/EXAMPLES.md`
- `sdk/docs/MIGRATION_GUIDE.md`
- `sdk/template/` — copy to start a new plugin

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VM_PLUGINS` | `1` | Enable plugin system |
| `VM_PLUGINS_DIR` | `plugins/` | Plugin directory |
| `VM_PLUGIN_PATH` | — | Extra plugin paths (OS pathsep) |
| `VM_PLUGIN_MARKETPLACE_URL` | — | Remote storefront catalog JSON URL |
| `VM_PLUGIN_CATALOG_URL` | — | Alias for marketplace URL |

## What is NOT changed

Event Bus, Orchestrator, LLM Dispatcher, Pipeline Engine, Performance Optimizer,
Monitoring Center, translation algorithms, existing UI (except `/plugins` page).

## Tests

```bash
python -m pytest tests/test_plugins.py -q
```
