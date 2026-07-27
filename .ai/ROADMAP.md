# Development Roadmap

## Completed (Stages 1–10)

- [x] Event-driven pipeline foundation
- [x] AI Orchestrator with resource management
- [x] LLM Dispatcher with failover
- [x] Adaptive chunk conveyor
- [x] Fault tolerance layer
- [x] AI Memory + Semantic Cache
- [x] Performance Optimizer
- [x] Monitoring Center
- [x] Plugin System + SDK
- [x] Autonomous Development Platform

## Future (Post-Stage 10)

- [ ] Distributed processing (remote LLM/TTS nodes) — local RemoteJobQueue GREEN; `target=cloud` hard-gated 501 until TubeDub Cloud server
- [x] Plugin Marketplace UI (local catalog + zip/dir install; remote via `VM_PLUGIN_MARKETPLACE_URL`)
- [ ] Team collaboration / corporate edition
- [ ] Web API for mobile clients
- [ ] Automatic test stand integration
- [ ] Cloud + local AI hybrid mode (local mirrors + OAuth scaffolding done; remote Drive/Graph/Dropbox file API sync after tokens still pending)
- [ ] Native VST2/VST3 bridge (deferred — FFmpeg FX presets are production path; do not fake native process)

## Current Focus

Stabilize platform, grow plugin ecosystem, reduce technical debt.

**2026-07-24:** P0 Language Validation + TTS hygiene (44.zip): no bare «відчути», pre-TTS phrase-loop/bleed guard, Meaning Fit refuses destructive shorten, language confidence/recovery.

**2026-07-23:** Honest cloud/OAuth hard-gates (RU/EN); offline Argos/Piper preference; minimal Coqui/XTTS clone path. Remaining: remote cloud API sync (post-token), native VST bridge, full TubeDub Cloud server, PyInstaller EXE.

*Updated by DocumentationSync — run `assistant.document()` to refresh.*
