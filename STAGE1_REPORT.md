# ЭТАП 1 — Отчёт об устранении критических проблем
**Дата:** 25.06.2026  
**Статус:** ✅ Завершён — все регрессионные тесты пройдены

---

## 1. Список изменённых файлов

| Файл | Тип изменения |
|------|---------------|
| `engines/dub_task_state.py` | **Новый** — общий реестр задач, TTL-эвикция, cleanup TTS |
| `api/auto_dub_api.py` | Импорт состояния из `dub_task_state`, touch/evict, сохранение `tts_files` |
| `api/studio_api.py` | Импорт состояния из `dub_task_state`, cleanup после mix |
| `requirements_desktop.txt` | Синхронизация версии `pywebview` |
| `tests/test_dub_task_state.py` | **Новый** — 6 unit-тестов lifecycle |
| `api/auto_dub_api.py` (syntax) | Исправлены pre-existing ошибки отступов (блокировали import) |

---

## 2. Устранённые проблемы (из аудита)

### V-01: AUTO_TASKS не эвикировался — утечка памяти
**Решение:** Новый модуль `engines/dub_task_state.py`:
- `init_auto_task()` — регистрация с `_created_at` / `_last_touch`
- `touch_task()` — продление TTL при polling статуса
- `evict_expired_auto_tasks()` — удаление задач:
  - `studio_ready`: TTL 6 часов
  - `done` / `error`: TTL 2 часа
  - `running` / `editing`: защищены от эвикции
  - Hard cap: 100 задач (удаляются самые старые)
- Вызов эвикции: при создании задачи и в `finally` пайплайна

### V-06: TTS MP3 не очищались в auto-dub пути
**Решение:**
- `cleanup_task_tts_files()` — удаляет segment MP3, extracted audio, timed track
- Сохраняет финальный MP4 и файлы при `keep_studio_assets=True`
- При `studio_ready`: assets сохраняются (`keep_studio_assets=True`)
- После успешного Studio mix: `_mark_studio_mix_done()` снимает флаг и удаляет temp-файлы
- При TTL-эвикции abandoned задач: cleanup автоматически

### V-03: Циклическая зависимость auto_dub_api ↔ studio_api
**Решение:**
- `AUTO_TASKS`, `AUTO_TASK_CONTROLS`, `STATE_LOCK` перенесены в `engines/dub_task_state.py`
- `studio_api` импортирует состояние из `dub_task_state`, а не из `auto_dub_api`
- `auto_dub_api` по-прежнему вызывает `publish_studio_ready()` из `studio_api` (lazy import)
- Цикл разорван на уровне module-level state

### V-15: Конфликт версий pywebview
**Решение:** `requirements_desktop.txt`: `pywebview>=4.4.1` → `pywebview>=5.0` (согласовано с `pyproject.toml`)

---

## 3. Обоснование изменений

| Изменение | Почему |
|-----------|--------|
| Отдельный `dub_task_state.py` | Единая точка владения данными задачи; разрыв circular import; минимальный diff в потребителях |
| TTL вместо немедленного удаления | Studio может открыться через часы после `studio_ready`; polling `/status` продлевает жизнь задачи |
| Cleanup только после mix или TTL | TTS-файлы нужны Studio для preview/regen/mix — удаление до mix сломало бы workflow |
| `_mark_studio_mix_done()` helper | DRY для двух remux-путей в studio_api |
| Сохранение `tts_files` / `mux_base_id` в task info | Точный список артефактов для cleanup без glob-угадывания |

---

## 4. Результаты регрессионного тестирования

```
python -m pytest tests/ -q
........................................................................ [ 37%]
........................................................................ [ 74%]
..................................................                       [100%]

197 passed (191 existing + 6 new)
```

Дополнительно:
```
python -c "import app; print('OK')"  → OK
```

---

## 5. Возможные риски

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Studio открыта > 6 ч без polling → задача эвикирована | Низкая | `touch_task()` при каждом `/status`; TTL настраивается через константы |
| Cleanup удалит нужный MP3 если `output_file` не записан | Низкая | `keep_studio_assets=True` до mix; keep_names включает output |
| Hard cap 100 задач удалит старую studio_ready | Очень низкая | Только если >100 одновременных незащищённых задач |
| Pre-existing syntax fixes в auto_dub_api могли изменить slot-fit поведение | Низкая | Тест `test_pipeline_slot_fit_marks_green_when_fits` проходит; логика восстановлена по комментариям |

---

## 6. Оставшиеся открытые вопросы

1. **ProjectSession (V-02)** — объявлен, но пути файлов не используют `session_dir` → Этап 2
2. **Три предиктора длительности (V-04)** → Этап 2 (после подтверждения эквивалентности)
3. **Append-only shared logs (V-11)** → Этап 3
4. **Полная i18n (V-18)** → Этап 2–3 (не блокирует стабильность)

---

## 7. Definition of Done — Этап 1

- [x] AUTO_TASKS TTL-эвикция реализована
- [x] Память освобождается после TTL / hard cap
- [x] TTS temp-файлы удаляются после Studio mix и при эвикции
- [x] Circular import state разорван через `dub_task_state`
- [x] pywebview версии синхронизированы
- [x] Полный регрессионный тест пройден
- [x] Поведение приложения не изменено (UI, алгоритмы перевода/TTS не тронуты)

**Готовность к Этапу 2:** ✅
