"""VideoMonster Storage Manager (Phase 1).

Единая точка доступа к проектам. Все операции CRUD, корзина, экспорт/импорт,
миграции, блокировки и восстановление сессий — через :class:`StorageManager`
или модульные функции-обёртки.
"""

from engines.storage.events import StorageEvent
from engines.storage.manager import (
    StorageManager,
    check_session_recovery,
    close_project,
    create_project,
    delete_project,
    empty_trash,
    export_project,
    get_statistics,
    get_storage_manager,
    import_project,
    list_projects,
    move_to_trash,
    open_project,
    restore_project,
    save_project,
    startup_storage,
)
from engines.storage.migration import STORAGE_VERSION
from engines.storage.model import ProjectRecord

__all__ = [
    "STORAGE_VERSION",
    "StorageEvent",
    "StorageManager",
    "ProjectRecord",
    "get_storage_manager",
    "startup_storage",
    "check_session_recovery",
    "create_project",
    "open_project",
    "save_project",
    "close_project",
    "delete_project",
    "move_to_trash",
    "restore_project",
    "empty_trash",
    "export_project",
    "import_project",
    "list_projects",
    "get_statistics",
]
