# ADR-018 — Voice Platform / TTS / Lip Sync 2.0 (Master Spec Part 7)

## Status

Accepted

## Context

TTS was Edge-centric and opaque to Dub Engine planning. Providers existed as
P9 stubs (`engines/tts_engines`) but lacked a voice-level platform: registry,
planner, multi-speaker memory, cache, failover, and Lip Sync 2.0 data packaging.

## Decision

1. Package: `engines/voice_platform/`
2. `VoiceProvider` is the only synthesis contract Dub/Scheduler may depend on
   (via platform orchestrator — never concrete engines)
3. Legacy `BaseTTSEngine` instances auto-wrap as `LegacyEngineAdapter`
4. Voice Registry + configurable Style Profiles (`data/voice_profiles.json`)
5. Voice Planner + Voice Memory enforce per-speaker identity for the project
6. Prosody / Emotion / Phoneme / Viseme / Lip Sync 2.0 produce data only
7. Cloning via `VoiceCloneAdapter`; Failover + Performance Cache + Metrics
8. Wired from `semantic_v3.phase2` as `meta["voice_platform"]` (planning);
   synthesis entry: `voice_platform.synthesize()`

## Consequences

- New TTS engines register as adapters — no Dub Engine changes
- auto_dub Edge path remains for production continuity; new code should use
  Voice Platform
- Full neural cloning backends remain optional deps (XTTS/OpenVoice stubs)
- Lip Sync is still data foundation (no face animation renderer)

## Related

- ADR-016 Dub Engine 2.0
- ADR-017 Studio QA
- `docs/VOICE_PLATFORM_PART7_REPORT.md`
