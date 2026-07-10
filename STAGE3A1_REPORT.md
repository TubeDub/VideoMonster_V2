# Этап 3A.1 — Архитектурный контракт целостности данных пайплайна

**Версия:** 1.25.1 | **Статус:** Реализовано

---

## 1. Инварианты (TZ §1)

| # | Инвариант | Реализация |
|---|-----------|------------|
| 1 | Immutable Segment Model | `engines/pipeline_integrity/segment.py` — frozen `Segment`, `evolve()` |
| 2 | Identity Preservation | `segment_id` + guards на цепочку text/file/timing |
| 3 | Index-Free Pipeline | `segment_id` обязателен; `merged_into_id` для merge; index только локально |
| 4 | No Audio Reuse | `ArtifactRegistry` — один filename → один `segment_id` |
| 5 | Atomic Commit | `StageTransaction` / `run_stage_atomic()` |
| 6 | No Silence Gaps | делегировано Conflict Resolver (Stage 3A) |
| 7 | Validation Mandatory | `validation_always_enabled()` → всегда `True` |
| 8 | No Hidden Heuristics | `enforce_or_raise()` — без тихих fix |

---

## 2. Иерархия исключений (TZ §2)

`engines/pipeline_integrity/exceptions.py`:

- `PipelineIntegrityError` (base)
- `PipelineIdentityError`
- `PipelineAudioIdentityError`
- `RuntimeIntegrityError`
- `StageSnapshotIntegrityError`
- `ArtifactIntegrityError`
- `PipelineValidationError`

---

## 3. Эшелонированная защита (TZ §3)

| Уровень | Модуль | Функция |
|---------|--------|---------|
| 1 Architecture Guard | `guards.ArchitectureGuard` | `segment_id`, уникальность |
| 2 Runtime Integrity Guard | `guards.RuntimeIntegrityGuard` | merge pointers, timing_map, TTS presence |
| 3 Stage Snapshot Guard | `guards.StageSnapshotGuard` | whitelist `stage_contracts.py` |
| 4 Artifact Integrity Guard | `guards.ArtifactIntegrityGuard` + `artifact_registry.py` | SHA-256, No Audio Reuse |
| 5 Pipeline Validator | `guards.PipelineValidator` | финальная проверка перед studio handoff |

Оркестратор: `PipelineIntegrityCoordinator`.

---

## 4. Интеграция в пайплайн

`api/auto_dub_api.py`:

| Точка | Действие |
|-------|----------|
| `_segments_data_entries()` | сохранение `segment_id` из предыдущего прогона |
| После entries | `assign_segment_ids()` |
| Перед TTS | `begin_stage("tts")` |
| После TTS + voice FX | `register_tts_artifacts()` + `end_stage("tts")` |
| slot_fit | `begin_stage` / `end_stage("slot_fit")` |
| После equalize | `validate_pipeline(stage="studio_handoff")` |
| task info | `pipeline_integrity` JSON (profile + artifacts) |

Merge TTS: `merged_into_id` записывается вместе с legacy `merged_into`.

---

## 5. Stage Allowed Mutations

`engines/pipeline_integrity/stage_contracts.py` — белые списки для:

`stt`, `translate`, `tts`, `slot_fit`, `timing`, `studio_handoff`

---

## 6. Rollback Contract

`engines/pipeline_integrity/rollback.py`:

- `StageTransaction.begin()` — deepcopy segments + фрагмент task_info
- `rollback()` — восстановление при `PipelineIntegrityError`
- `run_stage_atomic()` — обёртка для атомарных этапов

---

## 7. Performance Budget

`GuardProfile` в coordinator:

- `architecture_ms`, `runtime_ms`, `snapshot_ms`, `artifact_ms`, `validator_ms`, `total_ms`
- Сохраняется в `task["info"]["pipeline_integrity"]["profile"]`

---

## 8. Тесты

`tests/test_pipeline_integrity.py` — **15 unit-тестов**:

- Segment model (2)
- Architecture Guard (2)
- Runtime Guard (2)
- Stage Snapshot (2)
- Artifact / No Audio Reuse (2)
- Pipeline Validator (2)
- Rollback (1)
- Validation mandatory (1)
- Coordinator bootstrap (1)

**Полный прогон:** `python -m pytest tests/ -q` → **237 passed**

---

## 9. DoD (TZ §7)

| Критерий | Статус |
|----------|--------|
| Все уровни защиты MUST | ✅ |
| Rollback Contract | ✅ |
| 225+ автотестов PASSED | ✅ 237 |
| Golden Regression | ⏳ видео не в repo |

---

## 10. Новые файлы

```
engines/pipeline_integrity/
  __init__.py
  exceptions.py
  segment.py
  stage_contracts.py
  artifact_registry.py
  guards.py
  rollback.py
tests/test_pipeline_integrity.py
STAGE3A1_REPORT.md
```

**Изменён:** `api/auto_dub_api.py` (segment_id, guards hooks, merged_into_id)

**Не изменялись:** Translation, TTS, Timing, Conflict Resolver, Mix, FFmpeg, UI алгоритмы.
