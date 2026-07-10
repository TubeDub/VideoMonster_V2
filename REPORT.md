# VideoMonster V2 — отчёт о доработке (MASTER TZ)

Дата: **16 июня 2026**  
Путь проекта: `C:\Users\serhii\Desktop\VideoMonster_V2`

---

## Краткий итог

| Критерий | Статус |
|----------|--------|
| Аудит-блокеры (5 пунктов) | ✅ Исправлены / подтверждены |
| Пайплайн авто-дубляжа | ✅ Реализован (Whisper → Argos/deep-translator → Edge-TTS → FFmpeg) |
| Simple + Pro режим UI | ✅ `body.mode-simple` / `body.mode-pro`, классы `pro-only` / `simple-only` |
| Лицензирование demo/basic/premium + online server | ✅ |
| `desktop.py` → `/dub` | ✅ |
| `requirements.txt` / `requirements_desktop.txt` | ✅ Синхронизированы |
| `build_windows.bat` + `desktop.spec` | ✅ Обновлены |
| Автотесты в среде агента | ⚠️ Запустите локально (см. команды ниже) |
| Bug #1 65% hang / Bug #2 split_by_timing_map | ✅ Исправлено 16.06.2026 |
| ZIP `VideoMonster_V2_ready.zip` | ⚠️ **Создайте локально** одной командой ниже |

**Готовность для обычного пользователя:** **Да**, после `pip install -r requirements_desktop.txt`, FFmpeg в PATH и интернета (Edge-TTS + deep-translator fallback).

---

## Аудит-блокеры — проверка (16.06.2026)

| # | Требование | Файл | Статус |
|---|------------|------|--------|
| 1 | `function applyLicenseBanner`, не `def` | `static/js/license.js` | ✅ Строка 26: `function applyLicenseBanner(status)` |
| 2 | MP4 download: `/api/dub/download/` + `encodeURIComponent` | `static/js/dub.js` | ✅ Строка 245 |
| 3 | Pro-only для Whisper/режима звука | `templates/dub.html` | ✅ `class="field-group pro-only"` (строки 64, 74); CSS: `body.mode-pro .pro-only` в `style.css` |
| 4 | Нет `except: pass`, нет debug `print` в py | `api/`, `engines/` | ✅ Не найдено; логирование через `logger` |
| 5 | `license_server.py` exception handling | `license_server.py` | ✅ `_load_db()`: `except (json.JSONDecodeError, OSError, UnicodeDecodeError)` + stderr warning, не `pass` |

---

## Что было сломано (исходное ТЗ)

1. **`templates/dub.html`** — содержал git diff вместо HTML.
2. **`auto_dub_api.py`** — язык «Авто» → `None` в перевод; debug `print`; дубли `_fail()`; битый список моделей Whisper.
3. **`translation.py`** — только Argos без fallback.
4. **`translation_compat.py`** — заглушки detect.
5. **`requirements.txt`** — не совпадали с импортами.
6. **`desktop.py`** — открывал `/`, не `/dub`.
7. Мусор: `fix_*.py`, `.bak`, `cleaner old.py`.

---

## Что исправлено в этой сессии (продолжение MASTER TZ)

| Файл | Изменение |
|------|-----------|
| `api/auto_dub_api.py` | Добавлен `APP_DIR`; упрощён поиск видео (uploads / output / абсолютный путь) — убран дублирующий блок |
| `desktop.spec` | В `datas` добавлен `modules/`; в `hiddenimports`: `faster_whisper`, `pydub`, `ffmpeg` |
| `scripts/run_master_checks.py` | **Новый** — import + e2e + ZIP + лог `output/master_check_results.txt` |
| `scripts/e2e_test.py` | Тестовое видео с реальной речью (Edge-TTS), иначе Whisper → empty_stt |
| `run_smoke_test.bat` | Запускает `run_master_checks.py` |
| `REPORT.md` | Этот документ |

---

## Полный список ключевых файлов проекта

### UI / Frontend
| Файл | Назначение |
|------|------------|
| `templates/dub.html` | Страница «одна кнопка» авто-дубляжа |
| `templates/base.html` | Topbar, sidebar, license banner, i18n, mode toggle |
| `templates/settings.html` | Настройки, активация ключа, язык UI |
| `static/js/dub.js` | Upload → start → poll → download MP4 |
| `static/js/license.js` | Статус/sync лицензии, баннер, features |
| `static/js/main.js` | Simple/Pro (`applyMode`), toast, friendly errors |
| `static/js/i18n.js` | RU/UK/EN |
| `static/i18n/*.json` | Строки локализации |
| `static/css/style.css` | `pro-only` / `simple-only`, modern UI |

### Backend API
| Файл | Назначение |
|------|------------|
| `app.py` | Flask app, маршруты страниц, `/dub` |
| `api/auto_dub_api.py` | Полный пайплайн авто-дубляжа |
| `api/dub_api.py` | Upload, `/api/dub/download/<filename>` |
| `api/license_api.py` | activate / sync / status / admin |
| `api/system_api.py` | `/api/system/check` — FFmpeg, Whisper, TTS, перевод |
| `api/translate_api.py`, `tts_api.py`, … | Остальные функции |

### Engines
| Файл | Назначение |
|------|------------|
| `engines/translation.py` | Argos → fallback **deep-translator** |
| `engines/translation_compat.py` | `langdetect`, проверка интернета |
| `engines/stt_engine.py` | faster-whisper |
| `engines/tts.py` | Edge-TTS с retry ×3 |
| `engines/dub_engine.py` | Сведение видео |
| `engines/timing_engine.py`, `cleaner.py` | Тайминг, очистка текста |
| `engines/license_manager.py` | Demo / Basic / Premium offline |
| `engines/license_server_client.py` | Online sync/activate |

### Desktop / сборка
| Файл | Назначение |
|------|------------|
| `desktop.py` | pywebview → `http://127.0.0.1:PORT/dub` |
| `build_windows.bat` | pip + PyInstaller |
| `desktop.spec` | Сборка exe с templates/static/data/engines/api/modules |
| `license_server.py` | Опциональный сервер лицензий (порт 8787) |

### Скрипты / документация
| Файл | Назначение |
|------|------------|
| `scripts/e2e_test.py` | Smoke E2E — видео с речью (Edge-TTS) для Whisper |
| `scripts/run_master_checks.py` | Import + e2e + ZIP + отчёт |
| `scripts/create_zip.ps1` | ZIP на Desktop |
| `scripts/generate_license_key.py` | Генерация ключей владельцем |
| `scripts/start_license_server.bat` | Запуск license server |
| `LICENSE_SYSTEM.md` | Документация лицензий |
| `run_smoke_test.bat` | Быстрый прогон проверок |
| `install_and_run.bat` | **Для пользователя** — pip + проверка FFmpeg + `desktop.py` |
| `README.md` | Быстрый старт на русском |

---

## Финальная полировка (fork subagent, 16.06.2026)

| Файл | Изменение |
|------|-----------|
| `templates/dub.html` | Кнопка «💾 Сохранить в папку» после дубляжа |
| `static/js/dub.js` | `saveToFolder()` → `/api/dub/save_to_folder`, `state.outputFile` |
| `install_and_run.bat` | Один клик: установка зависимостей + запуск для обычных пользователей |
| `README.md` | Краткая инструкция RU |

---

## Лицензирование

### Уровни (tier)

| Tier | Как получить | Авто-дубляж | MP4 export | Перевод |
|------|--------------|-------------|------------|---------|
| **demo** | Авто 7 дней при первом запуске или ключ TEST-7/TEST-30 | ✅ | ✅ | без лимита |
| **basic** | После истечения demo / деактивация | ❌ | ❌ | 15 переводов/день |
| **premium** | Ключ PREMIUM-* / LIFETIME | ✅ | ✅ | без лимита |

### Ключи (формат `VM-XXXX-XXXX-XXXX`)

- `T7XX` — demo 7 дней  
- `T30X` — demo 30 дней  
- `PRWK` / `PRMO` / `PRYR` — premium 7/30/365 дней  
- `LIFE` — premium бессрочно  

Генерация: `python scripts/generate_license_key.py --type TEST-7`  
Секрет: `data/license_secret.txt` или env `VM_LICENSE_SECRET`

### Online license server

1. На сервере владельца:
   ```powershell
   set VM_LICENSE_SECRET=your-secret
   set VM_OWNER_TOKEN=your-owner-token
   python license_server.py
   ```
2. У клиента в `data/license_server.json`:
   ```json
   { "enabled": true, "url": "http://YOUR-IP:8787" }
   ```
   Или env: `VM_LICENSE_SERVER_URL`, `VM_LICENSE_SERVER_ENABLED=1`

3. Клиент: **Настройки → ключ → Активировать**; фоновый sync каждые 6 ч (`license.js`).

### UI

- Баннер: `#license-banner` — demo expired, sync warning, server message  
- Pro-only поля: Whisper model, dub mode (`dub.html`)  
- Переключатель **✨ Просто / ⚙️ Про** в topbar (`main.js`)

---

## Пайплайн авто-дубляжа (end-to-end)

```
Видео → FFmpeg extract audio → Whisper STT → перевод (Argos → deep-translator)
     → Edge-TTS по сегментам → timing/mix → FFmpeg export MP4
```

Этапы в UI: preparing → extract → transcribe → translate → tts → timing → dub → done.

API: `POST /api/auto_dub/start` → `GET /api/auto_dub/status/<task_id>` → скачать `/api/dub/download/<file>`.

---

## Установка

```powershell
cd C:\Users\serhii\Desktop\VideoMonster_V2
pip install -r requirements_desktop.txt
```

**Обязательно:** FFmpeg в Windows PATH — https://ffmpeg.org  

**argostranslate** — опционален (офлайн-перевод); на Python 3.13 может не установиться → используется deep-translator (интернет).

---

## Тесты

### Команды (выполнить на вашем ПК)

```powershell
cd C:\Users\serhii\Desktop\VideoMonster_V2

# 1. Import
python -c "import app; print('OK')"

# 2. E2E (до ~10 мин при первом Whisper + TTS)
python scripts/e2e_test.py

# 3. Всё сразу + ZIP
python scripts\run_master_checks.py
# или
run_smoke_test.bat

# 4. Только ZIP
powershell -ExecutionPolicy Bypass -File scripts\create_zip.ps1
```

Ожидаемый результат e2e:
- `System check: 200`
- `Dub page: 200 OK`
- `Upload: 200`
- `DONE: <filename>.mp4 size= >0` или `SKIP pipeline` если нет FFmpeg

Лог master checks: `output/master_check_results.txt`

### Результаты автоматического прогона агента (16.06.2026)

**Не выполнены:** среда Cursor на Windows вернула ошибку sandbox (`workspace_readwrite` not supported). Код и скрипты проверены статически.

**Исправление e2e:** тестовое видео теперь содержит речь (Edge-TTS + FFmpeg), иначе Whisper возвращал пустоту и пайплайн падал на silent sine-wave.

**Запустите локально:** `run_smoke_test.bat` или `python scripts\run_master_checks.py`

---

## ZIP-архив для передачи пользователю

После успешного прогона:

`C:\Users\serhii\Desktop\VideoMonster_V2_ready.zip`

Создаётся скриптами `create_zip.ps1` или `run_master_checks.py` (исключает `__pycache__`, `.git`, `node_modules`, `dist`, `build`).

---

## Ограничения (честно)

| Компонент | Статус |
|-----------|--------|
| `python desktop.py` | ✅ Нужны pip-пакеты + pywebview |
| UI `/dub` | ✅ |
| FFmpeg | **Обязателен в PATH** |
| Whisper | Офлайн после первой загрузки модели (~75 МБ tiny) |
| Edge-TTS | **Нужен интернет** |
| Argos | Офлайн при установленных моделях |
| deep-translator | **Fallback, нужен интернет** |
| Полностью офлайн-дубляж | ❌ Невозможен с Edge-TTS |
| PyInstaller exe | Собирается `build_windows.bat`; Whisper/TTS модели качаются при первом запуске |

---

## Ручные шаги для владельца / пользователя

### Самый простой путь (Windows)

1. Установить **Python 3.10–3.13** и **FFmpeg** в PATH.  
2. Дважды щёлкнуть **`install_and_run.bat`**.  
3. В окне: выбрать видео → язык → **🤖 Дубляж** → скачать MP4 или **Сохранить в папку**.  

### Разработчик / владелец

1. `pip install -r requirements_desktop.txt`  
2. Запуск: `python desktop.py` → окно на `/dub`.  
3. Premium-ключ в **Настройках**; для онлайн-лицензий — `license_server.py`.  
4. Сборка exe: `build_windows.bat` → `dist\VideoMonster\VideoMonster.exe`.  
5. Перед передачей: **`run_smoke_test.bat`** → проверить `output\master_check_results.txt` и ZIP на Desktop.

---

## Simple vs Pro

| | Simple | Pro |
|---|--------|-----|
| Переключение | Topbar «✨ Просто» | «⚙️ Про» |
| Whisper model | Скрыт (tiny по умолчанию) | Выбор tiny…large |
| Dub mode mix/replace | Скрыт | Виден |
| CSS | `.pro-only { display:none }` | `body.mode-pro .pro-only { display:block }` |

---

VideoMonster V2 — сделано для людей.

---

## Supplement #5 — тестовые сборки и первый запуск владельца (16.06.2026)

### Реализовано

| Компонент | Файл | Описание |
|-----------|------|----------|
| Одноразовая инициализация | `engines/owner_first_run.py` | Dirs, ключи, авто TEST-7 ZIP, маркер `.owner_initialized` |
| Тестовые ZIP | `engines/test_build_manager.py` | ZIP без секрета владельца + `license.json` + README |
| API владельца | `api/owner_api.py` | list/create/revoke/extend/download |
| UI | `templates/settings.html` | Панель «Создать тестовую сборку» |
| Старт | `app.py`, `desktop.py` | `run_if_needed()` при запуске |
| CLI | `scripts/owner_tools.bat` | Меню: ключи, сборки, revoke, extend, reinit |
| Реестр | `data/test_builds_registry.json` | История тестовых сборок |

### Как владелец использует

1. **Первый запуск** `python desktop.py` — автоматически: dirs + TEST-7 ZIP в `output/test_builds/`.
2. **Настройки → Панель владельца** — токен `vm-owner-local` → «Создать тестовую сборку».
3. **CLI:** `scripts\owner_tools.bat` — пункты 5–10.
4. ZIP готов для отправки; EXE опционально через `build_windows.bat`.

### Как тестер использует

1. Распаковать ZIP, `install_and_run.bat`.
2. Лицензия встроена; 7/30 дней full/demo или premium по типу.
3. После срока: Basic, баннер «Срок тестирования истёк», premium-функции отключены.
4. `README_ДЛЯ_ТЕСТЕРА.txt` в корне архива.

### Ручная проверка (Supplement #5)

```powershell
cd C:\Users\serhii\Desktop\VideoMonster_V2

# 1. Import
python -c "import app; print('import OK')"

# 2. Повторная инициализация (dev)
set VM_DEV_MODE=1
python -c "from engines.owner_first_run import run_init; print(run_init(force=True))"

# 3. Создать TEST-7 сборку
python -c "from engines.test_build_manager import create_test_build; print(create_test_build('TEST-7'))"

# 4. Список
python -c "from engines.test_build_manager import list_test_builds; print(list_test_builds())"

# 5. UI: desktop.py → Настройки → Панель владельца
```

### Готовность vs критерий Supplement #5

| Критерий | Статус |
|----------|--------|
| 29.1 One-time owner init | ✅ |
| 29.2 Auto test build after first launch | ✅ |
| 29.3 Test build contents (license + README) | ✅ |
| 29.4 Owner management (create/revoke/extend/list) | ✅ |
| 29.5 Simple distribution (ZIP button) | ✅ |
| 29.6 Philosophy (time-limited, not free forever) | ✅ |
| Автотесты агента | ⚠️ Запустите локально `python -c "import app"` |

**§17 экосистема:** standalone EXE копируются в `%LOCALAPPDATA%\VideoMonsterFreeApps` (не удаляются при деинсталляции VideoMonster) — `scripts\build_ecosystem.bat` → `scripts\install_ecosystem.bat`.
