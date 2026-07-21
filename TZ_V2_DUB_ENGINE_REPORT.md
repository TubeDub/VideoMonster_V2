# Dub Engine Stabilization Roadmap — TZ v2.0 Report

**Дата:** 11 июля 2026  
**Статус:** Foundations P0–P14 реализованы поверх Freeze TZ

## Соответствие фазам

| Фаза | Содержание | Статус |
|------|------------|--------|
| P0 | Lock, Immutable, Owner, FSM (+HANDOFF), Contracts×5 | ✅ |
| P1 | Engine split, Scheduler API, import lint | ✅ |
| P2 | AudioTimingOptimizer | ✅ |
| P3 | UUID, TTS lifecycle, WAV gate before Studio | ✅ |
| P4 | Runtime Integrity Validator + Diagnostic ZIP | ✅ |
| P5 | Diagnostics data layer (OpenDDF + observability APIs) | ✅ partial (API/data; UI viewers — follow-up) |
| P6 | Error Recovery strategies | ✅ |
| P7 | Perf budgets (+ runtime≤10ms, diagnostics≤5ms) | ✅ |
| P8 | Observability (metrics, history, health, graph) | ✅ |
| P9 | TTS adapters (+ Edge, ElevenLabs) | ✅ |
| P10 | Plugin registry | ✅ |
| P11 | Architecture/import lint tests (CI-ready suite) | ✅ |
| P12 | Golden dataset scaffold | ✅ |
| P13 | Deterministic fingerprint / golden compare | ✅ |
| P14 | Crash checkpoint / resume | ✅ |
| P15 | DoD — см. ниже | ⏳ частично (нужен полный golden+e2e на проде) |
| **P16** | **Production Hardening** | ✅ foundations (CI fast + lab long-run opt-in) |
| **P17** | **Quality Certification & Release Governance** | ✅ foundations |
| **P3.1** | **Runtime Integrity Hotfix (TTS/Handoff/Registry)** | ✅ |

## Новые модули (v2)

- `engines/pipeline_integrity/runtime_validator.py`
- `engines/pipeline_integrity/error_recovery.py`
- `engines/pipeline_integrity/crash_recovery.py`
- `engines/pipeline_integrity/plugin_registry.py`
- `engines/pipeline_integrity/golden_dataset.py`
- `engines/pipeline_integrity/observability.py`
- `engines/production_hardening/` (P16)
- `engines/release_governance/` (P17)
- Contracts: scheduler/studio/tts versions
- FSM: `HANDOFF` between MERGED and EXPORTED

## Тесты

```
tests/test_dub_engine_tz_v2.py
tests/test_dub_engine_import_lint_v2.py
+ prior freeze suite (P0–P9)
→ PASS
```

## Ограничения

1. P5 UI viewers (Timeline/Pipeline/Metrics Dashboard) — backend/API готов; полноценный UI — отдельный фронтенд-этап.
2. P12 golden — scaffold + fingerprint; коллекция 20 фильмов / 10000 сегментов наполняется отдельно.
3. P11 — тесты готовы к CI; подключение в GitHub Actions/Makefile — follow-up если ещё нет.
4. Neural TTS backends — adapters + availability; установка пакетов на машине заказчика.
5. Полный e2e DoD P15 требует прогона на golden dataset в проде.
6. P16 lab: 8h/24h long-run (`python scripts/run_p16_hardening.py --long-run-sec 28800`) — обязателен перед релизом.
7. P17: `make certify` / `python scripts/run_p17_certify.py --promote` — Quality Gates + Release Certificate.

## Принцип

Root-cause: `slot_ms=0` false overflow уже исправлен; Runtime Validator останавливает pipeline вместо silent continue.
