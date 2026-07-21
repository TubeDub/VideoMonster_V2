# P0 — Translation Lock, Single Owner, Immutable Segment

**Дата:** 11 июля 2026  
**Статус:** Реализовано — ожидает подтверждения заказчика  
**Контракты:** `translation_contract_version=1`, `dub_contract_version=1`

---

## 1. Цель фазы

Зафиксировать границу **Text First → Validation → TRANSLATION LOCK → Audio First**:
после LOCK текст сегмента неизменяем; пайплайн получает FSM без rollback;
владельцы полей и версии контрактов зафиксированы.

---

## 2. Изменённые / новые файлы

### Созданы
| Файл | Назначение |
|------|------------|
| `engines/pipeline_integrity/pipeline_state.py` | FSM: NEW→…→EXPORTED, запрет rollback |
| `engines/pipeline_integrity/contract_versions.py` | `translation_contract_version` / `dub_contract_version` = 1 |
| `engines/pipeline_integrity/translation_lock.py` | LOCK, LOCKED_TEXT_FIELDS, Single Owner, Immutable Segment |
| `tests/test_translation_lock_p0.py` | Unit + architecture + regression тесты P0 |
| `P0_TRANSLATION_LOCK_REPORT.md` | Этот отчёт |

### Изменены
| Файл | Изменение |
|------|-----------|
| `engines/pipeline_integrity/exceptions.py` | `PipelineStateError`, `ContractVersionError`, `TranslationLockError` |
| `engines/pipeline_integrity/stage_contracts.py` | стадии `validate` / `locked` / `scheduler`; POST_LOCK поля |
| `engines/pipeline_integrity/guards.py` | StageSnapshotGuard блокирует текст при `translation_locked` |
| `engines/pipeline_integrity/__init__.py` | экспорт P0 API |
| `engines/translation_validation.py` | guard в `apply_translated_text_to_segment`; `apply_translation_lock_after_validation` |
| `api/auto_dub_api.py` | LOCK после Validation; LOCK перед TTS; `LOCKED→TTS_READY` |
| `engines/closed_loop_timing.py` | после LOCK — overflow вместо rewrite текста |

---

## 3. Архитектурные изменения

### TRANSLATION LOCK
- После Validation финальный текст фиксируется: `translation_locked=True` на сегменте и в `task.info`.
- Запрещены изменения: `translated_text`, `semantic_text`, `grammar_text`, `corrected_text`, `rewritten_text` + алиасы репозитория (`plain_text`, `translation_text`, `timing_text`, `final_text`, `text_for_tts`, `voice_input`, `text`, `glossary`, `context`, `speaker_text`).
- Любая попытка → `TranslationLockError` (нет silent fix).

### Single Owner
Задокументировано и проверяемо через `FIELD_OWNERS` / `OWNER_FIELD_GROUPS` / `assert_owner_may_write`:

| Данные | Владелец |
|--------|----------|
| Исходный текст | Whisper |
| Перевод | Translation Engine |
| Временные метки | Scheduler |
| Аудиофайл | TTS Engine |
| Итоговая дорожка | Merge Engine |

### Immutable Segment (после LOCK)
Разрешены только timing/audio поля (`start_time`/`end_time`/`playback_rate`/`silence_trim`/`stretch_factor` + существующие ms/file/overflow поля).

### Versioned Contracts
При LOCK штампуются:
- `translation_contract_version: 1`
- `dub_contract_version: 1`

Mismatch → `ContractVersionError`.

### Pipeline State Machine
```
NEW → TRANSCRIBED → TRANSLATED → VALIDATED → LOCKED
  → TTS_READY → SCHEDULED → MERGED → EXPORTED
```
Обратные переходы запрещены (`LOCKED → TRANSLATED` → `PipelineStateError`).

### Точки интеграции
1. Orchestrator path: после `write_translation_validation_json` → `apply_translation_lock_after_validation`.
2. Pre-TTS safety net: если lock ещё не стоит → применяется перед `begin_stage("tts")`.
3. Closed-loop: при lock пишет `overflow` / `overflow_locked`, **не** меняет текст.

---

## 4. Результаты тестов

### Unit + Architecture + Regression (P0)
```
tests/test_translation_lock_p0.py          — PASS (все)
```

Покрытие:
- LOCK / guards / immutable timing
- FSM forward + rollback forbidden
- Contract versions stamp/mismatch
- Single Owner architecture
- Closed-loop overflow instead of rewrite

### Regression (существующие integrity)
```
tests/test_pipeline_integrity.py           — PASS
tests/test_stage_snapshot_guard.py         — PASS
tests/test_tts_segment_fields.py           — PASS
tests/test_passive_openddf_integration.py  — PASS
tests/test_closed_loop_timing.py           — PASS
tests/test_translation_validation.py       — PASS
```

---

## 5. Критерии приёмки P0

| Критерий | Статус |
|----------|--------|
| LOCK работает, guards блокируют изменение текста | ✅ |
| Single Owner задокументирован и проверяется | ✅ |
| Immutable Segment enforced | ✅ |
| Contract versions фиксируются | ✅ |
| State Machine не допускает rollback | ✅ |
| Unit + architecture + regression pass | ✅ |
| Отчёт по фазе предоставлен | ✅ |

---

## 6. Известные ограничения

1. **Не все call-sites пайплайна продвигают FSM на каждом шаге** (STT→TRANSCRIBED и т.д.). `apply_translation_lock_after_validation` поднимает состояние до VALIDATED/LOCKED при необходимости; полный wiring всех шагов — уточнение в P1 при разделении engine.
2. **Adaptation / Grammar / Semantic agents** до LOCK по-прежнему могут менять текст (это штатно). После LOCK их запись блокируется guards / `apply_translated_text_to_segment`.
3. **Closed-loop** при lock не устраняет overlap audio-only (это зона **P2** AudioTimingOptimizer); сейчас только помечает overflow.
4. **ADR-001…** по ТЗ относятся к **P4** — в P0 не создавались.
5. **Разделение Dub Engine / Scheduler API** — **P1**, не входит в эту фазу.

---

## 7. Решение заказчика

Переход к **P1** допускается только после явного подтверждения.

Ожидаемый ответ: **«P0 принято»** / **«P0 доработать: …»**.
