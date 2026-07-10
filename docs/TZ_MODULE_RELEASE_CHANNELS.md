# ТЗ: Система каналів релізів, режим розробника та керування модулями TubeDub

**Версия:** 1.0 (реализовано в коде)  
**Дата:** 2026-06-17

## Реализация

| Компонент | Путь |
|-----------|------|
| Реестр модулей (defaults) | `data/module_registry.json` |
| Локальные overrides | `data/module_registry.local.json` |
| Engine | `engines/module_registry/registry.py` |
| API | `api/modules_api.py` |
| Dynamic menu | `static/js/modules_nav.js` |
| Module Manager UI | `/dev/modules` → `templates/dev_modules.html` |
| Route guard | `app.py` → `@app.before_request` |
| Тесты | `scripts/test_module_registry.py` |

## Статусы

| Статус | Emoji | Production menu | Developer menu |
|--------|-------|-----------------|----------------|
| stable | 🟢 | ✓ если visible_to_users | ✓ |
| beta | 🟡 | ✓ если show_beta_to_users | ✓ |
| development | 🔴 | ✗ | ✓ |
| disabled | ⚫ | ✗ | ✓ (серый) |

## Developer Mode

Активен если:
- `VM_DEVELOPER_MODE=1` или `VM_DEV_MODE=1`, **или**
- копия владельца (`is_owner_host`) **и** UI режим 🔧 Dev (`X-VM-Client-Dev-Mode: 1`)

## API

- `GET /api/modules/nav` — меню для sidebar
- `GET /api/modules/registry` — все модули (dev only)
- `PATCH /api/modules/registry/{id}` — `{ "action": "stable"|"beta"|"development"|"disable"|"hide_users"|"show_users" }`
- `POST /api/modules/settings` — `{ "show_beta_to_users": true }`

## Принцип

Скрытие модуля **не удаляет код** — меняется только видимость в меню и доступ к маршрутам для production-пользователей.
