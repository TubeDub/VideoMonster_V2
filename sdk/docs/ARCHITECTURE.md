# Plugin Architecture

```
plugins/{name}/plugin.json  →  PluginManager.discover()
                                    ↓
                              VersionManager.check()
                              DependencyResolver.resolve()
                                    ↓
                              import plugin.py → Plugin class
                                    ↓
                              sandbox initialize()
                                    ↓
                              Capability Index
                                    ↓
                         SDK register_* hooks
```

The core (Event Bus, Orchestrator, Pipeline, etc.) is **never modified**.
Plugins register capabilities; the system resolves providers by capability name.

## Distributed Processing (future)

Plugins declare `"execution_mode": "local|remote|hybrid"` and optional
`"remote_endpoint"` in manifest. Interfaces are ready; implementation deferred.
