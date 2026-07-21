# P1 — Разделение Engine, Scheduler API, Architecture Tests

**Дата:** 11 июля 2026  
**Статус:** Реализовано — ожидает подтверждения заказчика  
**База:** P0 принято

---

## 1. Цель фазы

- Изолировать Dub Engine от Translation / LLM
- Сделать **Scheduler API** единственным способом менять timing после LOCK
- Закрепить architecture tests в тестовом наборе

---

## 2. Изменённые / новые файлы

### Созданы
| Файл | Назначение |
|------|------------|
| `engines/scheduler/__init__.py` | Публичный API Scheduler |
| `engines/scheduler/api.py` | `update_time`, `request_time`, `Scheduler` |
| `engines/scheduler/errors.py` | `SchedulerError` |
| `engines/dub/__init__.py` | Post-LOCK Dub Engine boundary (без LLM) |
| `tests/test_scheduler_p1.py` | Unit-тесты Scheduler |
| `tests/test_dub_engine_architecture_p1.py` | Architecture tests P1 |
| `P1_SCHEDULER_DUB_BOUNDARY_REPORT.md` | Этот отчёт |

### Изменены
| Файл | Изменение |
|------|-----------|
| `engines/dubbing_engine/engine.py` | Удалены импорты SSO/ADA/`translation_adapt`; адаптеры только через inject |
| `engines/pipeline_integrity/stage_contracts.py` | `start_ms`/`end_ms` только у стадии `scheduler` |
| `engines/pipeline_integrity/guards.py` | `STAGE_OWNER_MODULES["scheduler"]` → `engines.scheduler` |
| `api/auto_dub_api.py` | Slot-fit timing через `_scheduler_set_segment_slot` |
| `engines/regeneration.py` | Timing через `scheduler.update_time` |
| `engines/conflict_resolver.py` | `place_start` через Scheduler при наличии `segment_id` |
| `tests/test_translation_lock_p0.py` | Timing после lock — через Scheduler |

---

## 3. Архитектурные изменения

### Разделение Engine
```
Translation Engine (текст, LLM, адаптация)
        ↓ TRANSLATION LOCK (P0)
Dub Engine boundary: engines/dub + engines/dubbing_engine + engines/scheduler
        ↓ только звук / время
```

`DubbingEngine` больше **не импортирует**:
- `translation_adapt`, `smart_segment_optimizer`, `adaptive_dubbing_adapter`
- `ai_core`, Qwen, Ollama, Grammar/Semantic agents, Prompt Builder

Текстовая адаптация возможна только через опциональные inject-колбэки
(`adapt_fn` / `duration_adapt_fn`), которые предоставляет Translation layer **до LOCK**.

### Scheduler API
```python
scheduler.update_time(segments, segment_id, start_ms=..., end_ms=...)
scheduler.request_time(segments, segment_id, required_ms)
```

- Single Owner = `"Scheduler"` (`assert_owner_may_write`)
- Текст не трогает
- Может продвинуть FSM: `TTS_READY → SCHEDULED`

### Architecture Tests
| Правило | Тест |
|---------|------|
| Dub Engine импортирует ai_core / translation → fail | `TestDubEngineImportBoundary` |
| Scheduler вызывает Translation → fail | `TestSchedulerImportBoundary` |
| Прямое `start_ms`/`end_ms` вне allowlist → fail | `TestDirectTimingMutationForbidden` |
| `slot_fit` не может менять `start_ms` | StageSnapshotGuard |

---

## 4. Результаты тестов

```
tests/test_scheduler_p1.py                    PASS
tests/test_dub_engine_architecture_p1.py      PASS
tests/test_translation_lock_p0.py             PASS
tests/test_pipeline_integrity.py              PASS
tests/test_stage_snapshot_guard.py            PASS
tests/test_closed_loop_timing.py              PASS
tests/test_tts_segment_fields.py              PASS
tests/test_dubbing_engine.py                  PASS
```

---

## 5. Критерии приёмки P1

| Критерий | Статус |
|----------|--------|
| Dub Engine изолирован от Translation/LLM | ✅ |
| Scheduler API — единственный способ менять время (в pipeline hot path) | ✅ |
| Architecture tests в наборе | ✅ |
| Unit + regression pass | ✅ |
| Отчёт по фазе предоставлен | ✅ |

---

## 6. Известные ограничения

1. **`api/studio_api.py`** — ручные правки тайминга в Studio пока в allowlist AST-теста; полный перевод Studio→Scheduler — follow-up.
2. **`conflict_resolver`** — промежуточные mix-rows без `segment_id` ещё пишут `place_start` напрямую; при наличии `segment_id` — через Scheduler.
3. **StreamDub** (`engines/streamdub`) — отдельный параллельный стек; полная изоляция не входила в P1.
4. **AudioTimingOptimizer / no-overlap** — зона **P2**.
5. Inject-адаптеры в `DubbingEngine` по умолчанию `None` → Stage 2/5 pass-through (текст должен быть готов до LOCK).

---

## 7. Решение заказчика

Переход к **P2** допускается только после явного подтверждения.

Ожидаемый ответ: **«P1 принято»** / **«P1 доработать: …»**.
