# Storage Manager — Architecture (Phase 1)

## Цель

`StorageManager` — единственная точка доступа к проектам VideoMonster.
Phase 1 **не меняет** логику дубляжа, UI и пайплайн — создаёт надёжный
фундамент хранения для будущих версий.

## Структура модулей

```
engines/storage/
├── __init__.py       # публичный API
├── manager.py        # StorageManager + функции-обёртки
├── model.py          # ProjectRecord
├── paths.py          # StoragePaths (все пути на диске)
├── atomic.py         # атомарная запись (temp → fsync → rename)
├── locks.py          # потоковые + межпроцессные блокировки
├── events.py         # StorageEventBus
├── migration.py      # storage_version + миграции
├── recovery.py       # восстановление после сбоя
└── cleanup.py        # очистка временных файлов
```

## Расположение на диске

| Путь | Назначение |
|------|-----------|
| `projects/vm_storage/<project_id>/project.json` | Каноническая запись проекта |
| `projects/vm_storage/.trash/<project_id>/` | Корзина |
| `data/storage_index.json` | Индекс активных проектов |
| `data/storage_trash_index.json` | Индекс корзины |
| `data/storage_recovery.json` | Checkpoint для восстановления сессии |
| `data/storage_locks/<id>.lock` | Межпроцессные блокировки |
| `data/storage_events.jsonl` | Журнал событий |

### Legacy (автоимпорт при startup)

| Путь | Импортируется как |
|------|-------------------|
| `output/studio_sessions/<task_id>.json` | Проект с `source=legacy_studio_session` |
| `projects/tdproj/<id>/*.tdproj` | Проект с `source=legacy_tdproj` |

## storage_version

Каждый `project.json` содержит `storage_version` (текущая: **1**).
При открытии проекта с более старой версией автоматически запускается
цепочка миграций (`engines/storage/migration.py`).

Регистрация новой миграции:

```python
from engines.storage.migration import register_migration

def _migrate_v1_to_v2(data, project_dir):
    data["new_field"] = "value"
    data["storage_version"] = 2
    return data

register_migration(1, _migrate_v1_to_v2)
```

## Атомарная запись (§5)

Все записи JSON проходят через `atomic_write_json()`:

1. Запись во временный файл `.<name>.<random>.writing`
2. `flush()` + `fsync()` файла
3. `os.replace(tmp, target)` — атомарный rename
4. `fsync()` директории (POSIX)

При отключении питания на диске либо старый файл, либо новый — никогда частичный.

## Блокировки (§6)

Двухуровневая защита при записи:

1. **Потоковая** — `threading.RLock` на `project_id` (внутри процесса)
2. **Межпроцессная** — lock-файл с PID; протухшие блокировки
   (процесс-владелец мёртв) снимаются автоматически

`open_project()` захватывает lock; `close_project()` освобождает.

## Корзина (§4)

| Операция | API |
|----------|-----|
| Мягкое удаление | `move_to_trash(project_id)` |
| Восстановление | `restore_project(project_id)` |
| Окончательное удаление | `delete_project(id, permanent=True)` |
| Очистка корзины | `empty_trash()` |

## Восстановление сессии (§3)

При `open_project()` / `save_project()` записывается `data/storage_recovery.json`.
При `close_project()` — удаляется.

При следующем запуске `startup_storage()` проверяет recovery-файл.
Если проект существует и checkpoint свежий (< 7 дней) — API
`GET /api/storage/recovery` возвращает данные для UI.

## События (§7)

```python
from engines.storage import StorageEvent, get_storage_manager

mgr = get_storage_manager(app_dir)
mgr.subscribe(StorageEvent.PROJECT_SAVED, lambda evt, payload: print(payload))
```

События: `PROJECT_CREATED`, `PROJECT_OPENED`, `PROJECT_SAVED`,
`PROJECT_CLOSED`, `PROJECT_REMOVED`, `PROJECT_TRASHED`, `PROJECT_RESTORED`,
`PROJECT_DELETED`, `PROJECT_IMPORTED`, `PROJECT_EXPORTED`, `PROJECT_MIGRATED`,
`TRASH_EMPTIED`, `SESSION_STARTED`, `SESSION_FINISHED`, `STORAGE_CLEANUP`.

## Публичный API (§11)

```python
from engines.storage import (
    create_project, open_project, save_project, close_project,
    delete_project, move_to_trash, restore_project, empty_trash,
    export_project, import_project, list_projects, get_statistics,
    startup_storage, check_session_recovery,
)
```

## HTTP API

| Метод | Путь | Действие |
|-------|------|----------|
| GET | `/api/storage/projects` | Список проектов |
| POST | `/api/storage/projects` | Создать |
| GET | `/api/storage/projects/<id>` | Получить |
| POST | `/api/storage/projects/<id>/open` | Открыть |
| POST | `/api/storage/projects/<id>/save` | Сохранить |
| POST | `/api/storage/projects/<id>/close` | Закрыть |
| POST | `/api/storage/projects/<id>/trash` | В корзину |
| POST | `/api/storage/projects/<id>/restore` | Из корзины |
| DELETE | `/api/storage/projects/<id>?permanent=1` | Удалить |
| POST | `/api/storage/trash/empty` | Очистить корзину |
| GET | `/api/storage/recovery` | Проверить восстановление |
| GET | `/api/storage/statistics` | Статистика |

## Совместимость (§13)

Phase 1 **не трогает**:
- Whisper, Cleaner, Translator, TTS, Timing, Mix, Reader, Dub Engine
- `api/studio_api.py` — продолжает писать в `output/studio_sessions/`
- `engines/tubedub/project/store.py` — `.tdproj` autosave работает как прежде

Legacy-проекты импортируются в StorageManager при startup (read-only mirror).

## Следующие фазы (не в scope Phase 1)

- Перевод `studio_api._save_session` на StorageManager
- UI корзины и диалог восстановления
- Единый идентификатор `task_id` ↔ `project_id`
- Координированное удаление manifests/diagnostics/sessions

## Переменные окружения

| Переменная | Эффект |
|-----------|--------|
| `VM_SKIP_STORAGE_MANAGER=1` | Пропустить startup StorageManager |
| `OUTPUT_DIR` | Корень output (по умолчанию `output/`) |
