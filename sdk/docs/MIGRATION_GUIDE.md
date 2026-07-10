# Migration Guide

## API Versioning (§16)

- Core API version: `1.0.0`
- Old plugins with `minimum_api: "0.9.x"` are auto-disabled
- Deprecated APIs are listed in `VersionManager.DEPRECATED_APIS`
- New fields in `plugin.json` are optional — old manifests remain valid

## From Hardcoded Integrations

1. Wrap existing logic in a `BasePlugin` subclass
2. Move config to `plugin.json`
3. Replace direct imports with `sdk.core_api.register_*`
4. Place in `plugins/{name}/`

## Backward Compatibility Rules

- New capabilities are additive
- Removing a capability requires a major API bump
- `register_*` functions remain stable within API 1.x
