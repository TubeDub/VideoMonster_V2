# P3.1 — Runtime Integrity • TTS Lifecycle • Studio Handoff (Hotfix)

**Дата:** 11 июля 2026  
**Приоритет:** Critical  
**Scope:** только Dub Engine (Translation Engine не изменялся)

## Цель

Устранить `RUNTIME_INTEGRITY`, `TTS file not found`, `PIPELINE_AUDIO_IDENTITY`, потерю WAV/UUID и ошибки Studio Handoff без silent continue/recreate/rename/fallback.

## Архитектурные изменения

| § | Deliverable | Модуль |
|---|-------------|--------|
| 1–5 | Full TTS lifecycle FSM | `tts_artifact_lifecycle.py` (+ Synthesizing/Stored/HandoffReady/Exported) |
| 6 | Ownership | `wav_ownership.py` |
| 7 | Path validation | `path_validation.py` |
| 8 | Recovery search | `runtime_recovery.py` |
| 9 | Diagnostic ZIP v2 | `runtime_validator.write_diagnostic_zip` |
| 10–12 | Handoff / Scheduler / Merge gates | `runtime_validator` (`enforce_*`) |
| 13 | Runtime Registry | `runtime_registry.py` |
| 14 | Cleanup Manager | `cleanup_manager.py` (запрет до EXPORTED) |
| 15–16 | Graph + history | `runtime_graph.py` + segment `runtime_history` |
| 17 | Error taxonomy | `error_taxonomy.py` |
| 18–20 | Tests / stress / regression | `tests/test_runtime_integrity_p31.py` + legacy P3 |

## Wiring

- `api/auto_dub_api.py`: lifecycle → STORED→SCHEDULED; handoff sync Registry + HANDOFF_READY; cleanup через CleanupManager (не удаляет live WAV до EXPORTED).
- Handoff всегда получает актуальный `segments_data` в `info`.

## Тесты

```bash
python -m pytest tests/test_runtime_integrity_p31.py tests/test_uuid_lifecycle_p3.py -q
```

## Изменённые / новые файлы

- `engines/pipeline_integrity/tts_artifact_lifecycle.py`
- `engines/pipeline_integrity/wav_ownership.py` *(new)*
- `engines/pipeline_integrity/runtime_registry.py` *(new)*
- `engines/pipeline_integrity/path_validation.py` *(new)*
- `engines/pipeline_integrity/runtime_recovery.py` *(new)*
- `engines/pipeline_integrity/cleanup_manager.py` *(new)*
- `engines/pipeline_integrity/runtime_graph.py` *(new)*
- `engines/pipeline_integrity/runtime_validator.py`
- `engines/pipeline_integrity/error_taxonomy.py`
- `api/auto_dub_api.py`
- `tests/test_runtime_integrity_p31.py` *(new)*

## Ограничения

1. Lab long-run «100 фильмов / 1000 роликов / 10000 сегментов» — CI harness покрывает synthetic 500 сегментов; полный объём — lab.
2. Hash match enforce (`require_hash=True`) opt-in; по умолчанию hash пишется в Registry при upsert с `compute_hash=True`.
3. Translation Engine / LOCK / text immutability — не трогались (regression via existing P0/P1 suites).
4. Полный e2e Studio export → RELEASED advance ещё требует явного вызова на финальном export path (HANDOFF_READY уже на handoff).
