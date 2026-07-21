# ADR-019 — Platform SDK / Plugin System / Cloud / Ecosystem (Master Spec Part 8)

## Status

Accepted

## Context

VideoMonster core (Semantic, Translation, Decision, Dub, Scheduler, Studio) is
complete for automatic dubbing. Stage 9 already had `core/plugin_manager` and
`sdk/`, plus cloud scaffold, but Part 8 requires a unified Platform SDK:
protected core, permissions/sandbox, Event Bus, extension points, public API,
`.vmplugin` packages, marketplace architecture, cloud/team/webhooks/tokens,
and settings profiles — without modifying Core engines for new features.

## Decision

1. Package: `engines/platform_sdk/` — façade over existing plugin/cloud layers
2. P701 Core Protection — plugins adapt to core, never the reverse
3. Plugin Manager owns full lifecycle (Installed→…→Removed)
4. Interaction only via Public API + Event Bus + Extension Points
5. Distribution format: `.vmplugin` (manifest, version, signature, assets, code, docs)
6. Cloud / Team / Tokens / Webhooks / Marketplace / Settings Profiles as SDK modules
7. HTTP: `api/platform_sdk_api.py` (registered from `app_loader`, no core edits)
8. Existing `plugins/` manifests are discoverable; Dub/Translation/Decision
   engines remain unchanged

## Consequences

- New TTS/Translation/Exporters ship as plugins without touching Core
- Script language runtimes (JS/Lua) and live marketplace storefront are follow-ups
- OAuth cloud providers remain optional; local cloud façade is always available

## Related

- ADR-018 Voice Platform
- ADR-017 Studio QA
- TZ #9 `docs/plugin_system.md`
- `docs/PLATFORM_SDK_PART8_REPORT.md`
