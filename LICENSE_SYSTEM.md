# VideoMonster V2 — система лицензирования

## Режимы

| Режим | Описание |
|-------|----------|
| **Demo** | Полный доступ на срок ключа (7 или 30 дней). При первом запуске без ключа — автоматический тест 7 дней. |
| **Basic** | После окончания теста: интерфейс, проекты, SRT, Reader, ручной дубляж, **15 переводов в день**. |
| **Premium** | Полный доступ: авто-дубляж, MP4, TTS, пакетная обработка, быстрые модели Whisper. |

После истечения Demo приложение **не удаляется** — переходит в Basic с сообщением:

> «Срок тестирования истёк. Обратитесь к владельцу для продления.»

## Формат ключей

```
VM-XXXX-XXXX-XXXX
```

| Тип | Префикс | Срок |
|-----|---------|------|
| TEST-7 | T7XX | 7 дней Demo |
| TEST-30 | T30X | 30 дней Demo |
| PREMIUM-WEEK | PRWK | 7 дней Premium |
| PREMIUM-MONTH | PRMO | 30 дней Premium |
| PREMIUM-YEAR | PRYR | 365 дней Premium |
| LIFETIME | LIFE | Premium навсегда |

## Генерация ключей (владелец)

```powershell
cd C:\Users\serhii\Desktop\VideoMonster_V2
python scripts/generate_license_key.py TEST-7
python scripts/generate_license_key.py PREMIUM-MONTH
python scripts/generate_license_key.py LIFETIME
```

**Важно:** смените секрет в `data/license_secret.txt` или переменной `VM_LICENSE_SECRET` перед распространением.

## Удалённое управление (владелец)

Токен: `VM_OWNER_TOKEN` (по умолчанию `vm-owner-local` — смените в production).

### Продлить лицензию пользователю

```powershell
curl -X POST http://127.0.0.1:5199/api/license/admin/extend ^
  -H "Content-Type: application/json" ^
  -H "X-VM-Owner-Token: vm-owner-local" ^
  -d "{\"mode\": \"7\"}"
```

Режимы: `"7"`, `"30"`, `"lifetime"`.

### Отключить ключ

```powershell
curl -X POST http://127.0.0.1:5199/api/license/admin/revoke ^
  -H "Content-Type: application/json" ^
  -H "X-VM-Owner-Token: vm-owner-local" ^
  -d "{\"key\": \"VM-T7XX-ABCD-EFGH\"}"
```

### Сгенерировать ключ через API

```powershell
curl -X POST http://127.0.0.1:5199/api/license/admin/generate ^
  -H "Content-Type: application/json" ^
  -H "X-VM-Owner-Token: vm-owner-local" ^
  -d "{\"type\": \"TEST-7\"}"
```

## Офлайн-работа

- Лицензия хранится локально в `license.json`.
- Проверка ключей — без интернета (HMAC + локальный секрет).
- Раз в ~6 часов клиент пробует синхронизацию (`POST /api/license/sync`).
- Если интернета нет более 14 дней — предупреждение, **данные не теряются**, работа продолжается.

## Онлайн-сервер активации

### Запуск (владелец)

```powershell
set VM_LICENSE_SECRET=ваш-секрет
set VM_OWNER_TOKEN=ваш-токен
python license_server.py --port 8787
```

Или `scripts\start_license_server.bat`. База: `data/license_server_db.json`.

### Настройка клиентов

`data/license_server.json`:

```json
{ "enabled": true, "url": "http://ВАШ-IP:8787" }
```

Или `VM_LICENSE_SERVER_URL=http://...`

### Поведение

- Активация: сначала сервер `/v1/activate`, при сбое — офлайн-HMAC.
- Синхронизация: `/v1/sync` каждые 6 ч — продление/отзыв с сервера.
- Ключ привязан к `device_id`; перенос — через `/v1/admin/rebind`.

### Admin API сервера

- `POST /v1/admin/revoke` — отключить ключ
- `POST /v1/admin/extend` — `{ "key": "...", "days": 30 }` или `{ "lifetime": true }`
- `POST /v1/admin/generate` — `{ "type": "TEST-7" }`
- Заголовок: `X-VM-Owner-Token`

## Распространение

- Установщик (EXE) можно свободно раздавать.
- Без ключа: 7 дней автоматического теста, затем Basic.
- Premium/Demo по ключу от владельца.
- Пересланный установщик без ключа не даёт полный доступ навсегда.

## API для UI

- `GET /api/license/status` — текущий статус
- `POST /api/license/activate` — `{ "key": "VM-..." }`
- `POST /api/license/deactivate` — сброс до Basic
- `POST /api/license/sync` — обновить метку синхронизации

## Файлы

| Файл | Назначение |
|------|------------|
| `license.json` | Активная лицензия пользователя |
| `data/license_secret.txt` | Секрет подписи ключей (только у владельца) |
| `data/license_revoked.json` | Список отключённых ключей |
| `data/test_builds_registry.json` | Реестр тестовых ZIP-сборок |
| `data/.owner_initialized` | Маркер: инициализация владельца выполнена |
| `engines/license_manager.py` | Логика tier / features |
| `engines/owner_first_run.py` | Одноразовая инициализация владельца |
| `engines/test_build_manager.py` | Создание тестовых ZIP с встроенной лицензией |
| `engines/license_server_client.py` | Клиент онлайн-сервера |
| `license_server.py` | Сервер активации (отдельный процесс) |
| `data/license_server.json` | URL сервера для клиентов |
| `api/owner_api.py` | API панели владельца (тестовые сборки) |

## Первый запуск владельца (Supplement #5)

При **первом** запуске на копии владельца (есть `data/license_secret.txt`, нет `data/.owner_initialized`):

1. Создаются директории `output/`, `uploads/`, `projects/`.
2. Генерируются примеры ключей TEST-7 / TEST-30.
3. Автоматически создаётся тестовая ZIP-сборка TEST-7 в `output/test_builds/`.
4. Создаётся маркер `data/.owner_initialized` — повторный автозапуск не выполняется.

Повторная инициализация: `VM_DEV_MODE=1` или пункт 10 в `scripts/owner_tools.bat`.

Тестовые сборки **не содержат** `license_secret.txt` — только встроенный `license.json` с ключом.

## Тестовые сборки (владелец)

### UI

**Настройки → Панель владельца** (видна если есть `license_secret.txt`):

- Кнопка **«Создать тестовую сборку»**
- Выбор типа: TEST-7, TEST-30, PREMIUM-WEEK, PREMIUM-MONTH, PREMIUM-YEAR, LIFETIME
- Список сборок, скачивание ZIP, отключение / продление ключей

Токен: `X-VM-Owner-Token` (env `VM_OWNER_TOKEN`, по умолчанию `vm-owner-local`).

### CLI

```powershell
scripts\owner_tools.bat
```

### API

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/owner/status` | Статус инициализации |
| GET | `/api/owner/test-builds` | Список сборок |
| POST | `/api/owner/test-builds/create` | `{ "type": "TEST-7", "label": "..." }` |
| POST | `/api/owner/test-builds/revoke` | `{ "key": "VM-..." }` |
| POST | `/api/owner/test-builds/extend` | `{ "key": "...", "days": 7 }` |
| GET | `/api/owner/test-builds/download/<id>` | Скачать ZIP |

### Для тестера

1. Получить ZIP от владельца (Telegram / облако / USB).
2. Распаковать, установить Python + FFmpeg.
3. Запустить `install_and_run.bat`.
4. Лицензия уже активирована; после срока — Basic (программа не удаляется).
5. Прочитать `README_ДЛЯ_ТЕСТЕРА.txt` в архиве.

Установщик EXE: `build_windows.bat` → `dist\VideoMonster\VideoMonster.exe` (опционально).
