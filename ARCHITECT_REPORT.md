# ARCHITECT_REPORT — VideoMonster V2

**Аудитория:** второй ИИ-архитектор (не конечный пользователь)  
**Дата:** 16 июня 2026  
**Путь:** `C:\Users\serhii\Desktop\VideoMonster_V2`  
**Методология:** чтение кодовой базы (~150 файлов), `REPORT.md`, транскрипты агентов. **Git-репозитория в каталоге нет** (`.git` отсутствует) — `git log` / `git diff` недоступны; ниже — фактическое состояние файлов и описание работ из `REPORT.md` + статический анализ.

**Статус subagent `ad254a01` (Subtitle suite + ecosystem EXE):** **ЗАВЕРШЁН (код).** Subtitle suite и ecosystem-скрипты реализованы (см. §6). Каталог `dist/ecosystem/` пуст — артефакты EXE появятся только после локального запуска PyInstaller.

---

### 1. Какие файлы были изменены

| Путь | Что изменено | Зачем |
|------|--------------|-------|
| `api/auto_dub_api.py` | Полный пайплайн авто-дубляжа: `_prepare_translated_segments`, `_ensure_control`, посегментный TTS с прогрессом 65–80%, локализация ошибок, `APP_DIR`, поиск видео по нескольким путям, DubEngine с timeout | Устранить зависание на 65%, mismatch сегментов, довести до MP4 |
| `engines/cleaner.py` | `align_segments_to_timing_map`, восстановление в `split_by_timing_map` (auto-split / pad / merge), `_distribute_text_to_segments`, `_merge_lines_to_count` | Не падать на `timing_map=31, translated_lines=1` |
| `engines/translation.py` | `translate_segments()` — перевод по одному сегменту; цепочка Argos → `deep-translator` | Сохранить 1:1 с Whisper timing_map |
| `engines/tts.py` | `asyncio.wait_for` (120 с), retry ×3, проверка пустого файла | Защита от вечного TTS на шаге 65% |
| `engines/translation_compat.py` | Реальный `langdetect`, `has_internet()` через socket | Убрать заглушки detect |
| `templates/dub.html` | Восстановлен валидный HTML (раньше — git diff вместо разметки) | Страница «одна кнопка» авто-дубляжа |
| `static/js/dub.js` | Upload → start → poll → download MP4, `encodeURIComponent`, stall-hint 180 с, `tryResumeTask`, `saveToFolder` | UX авто-дубляжа без терминала |
| `static/i18n/ru.json`, `uk.json`, `en.json` | Строки для `/dub`, ошибок, шагов прогресса | i18n |
| `static/js/main.js` | Simple/Pro (`applyMode`), `vmUniversalImport`, friendly errors | Режимы UI + универсальный импорт (частично) |
| `static/js/license.js` | `function applyLicenseBanner` (не `def`) | Блокер аудита JS |
| `static/css/style.css` | `.pro-only` / `.simple-only`, modern UI | Pro скрывает Whisper и dub mode |
| `templates/base.html` | Sidebar, license banner, universal import input, навигация | Каркас приложения |
| `templates/settings.html` | Панель владельца (тестовые сборки, revoke/extend) | Supplement #5 |
| `app.py` | Регистрация `owner_api`, `import_api`, `run_if_needed()` при старте | Owner init + import detect |
| `desktop.py` | Старт на `/dub`, owner init в потоке Flask, обработка ошибок pywebview | Desktop entry для пользователя |
| `desktop.spec` | `datas`: templates/static/data/engines/api/modules; hiddenimports Whisper/pydub | PyInstaller-сборка |
| `build_windows.bat` | pip + PyInstaller | Сборка основного EXE |
| `api/dub_api.py` | Upload video/audio, download MP4, `save_to_folder`, cleanup | Ручной дубляж + сохранение результата |
| `license_server.py` | Явный exception handling в `_load_db()` | Аудит-блокер |
| `requirements.txt` | Синхронизация с импортами (flask, edge-tts, deep-translator, …) | Зависимости |
| `requirements_desktop.txt` | + `pywebview`, `pyinstaller` | Desktop-сборка |
| `scripts/e2e_test.py` | Тестовое видео с реальной речью (Edge-TTS + FFmpeg) | E2E не падал на empty STT |
| `REPORT.md` | Операционная документация MASTER TZ | Для владельца/тестера |

---

### 2. Какие новые файлы были созданы

| Файл | Назначение | Зачем |
|------|------------|-------|
| `api/import_api.py` | `POST /api/import/detect` — маршрутизация video→/dub, audio→/voice, subs→/studio | Universal Import (детект типа) |
| `api/owner_api.py` | CRUD тестовых сборок, first-run, download ZIP | Управление тестерами |
| `engines/owner_first_run.py` | Одноразовая инициализация владельца, dirs, ключи, TEST-7 ZIP | Supplement #5 |
| `engines/test_build_manager.py` | Сборка ZIP без секретов владельца + `license.json` + README | Распространение 7/30-дн. билдов |
| `engines/audio_formats.py` | FFmpeg-конвертация аудио в mono MP3 16 kHz | Поддержка форматов для STT (API готов, UI — нет) |
| `apps/reader_app.py` … `apps/audio_reader_app.py` (7 шт.) | Thin-wrapper: `VM_START_URL` + `desktop.main()` | Точки входа экосистемы EXE |
| `scripts/build_ecosystem.bat` | PyInstaller loop для 7 apps → `dist/ecosystem/` | Сборка экосистемы (не прогонялась) |
| `scripts/run_master_checks.py` | import + e2e + ZIP → `output/master_check_results.txt` | Автопроверки MASTER TZ |
| `scripts/test_segment_alignment.py` | Unit-тесты `split_by_timing_map` / `align_segments_to_timing_map` | Регресс Bug #2 |
| `scripts/owner_tools.bat`, `owner_tools.bat` | CLI-меню владельца | Альтернатива UI |
| `install_and_run.bat` | pip + проверка + `desktop.py` | One-click для пользователя |
| `run_smoke_test.bat` | Запуск `run_master_checks.py` | Smoke на ПК владельца |
| `LICENSE_SYSTEM.md` | Документация demo/basic/premium, ключи, server | Архитектура лицензий |
| `data/test_builds_registry.json` | Реестр тестовых сборок | Пустой `{}` — сборки ещё не создавались на этой копии |

**Примечание:** в корне также лежит React/Vite scaffold (`src/`, `package.json`, `vite.config.ts`) — **не подключён** к Flask-приложению; рабочий UI = Jinja + `static/js`.

---

### 3. Какие библиотеки были добавлены

| Библиотека | Назначение | Почему выбрана | Замены |
|------------|------------|----------------|--------|
| `flask>=3.0` | Web UI + REST API | Уже был каркас проекта | FastAPI не использовался |
| `edge-tts>=6.1` | Neural TTS (Microsoft) | Бесплатно, много голосов, без API-ключа | Coqui/Piper не интегрированы |
| `deep-translator>=1.11` | Онлайн-перевод fallback | Работает когда Argos недоступен (Py3.13) | Google API платный |
| `langdetect>=1.0` | Авто-язык текста | Лёгкая зависимость | Whisper detect — только для аудио |
| `faster-whisper>=1.0` | STT офлайн | Быстрее openai-whisper | openai-whisper — fallback в `stt_engine.py` |
| `pydub>=0.25` | Сведение аудио, timing | Стандарт для сегментов | — |
| `ffmpeg-python>=0.2` | Обёртка FFmpeg | Extract/mux в пайплайне | Прямые subprocess тоже используются |
| `pywebview>=4.4` | Desktop-окно | Native feel без Electron | Electron не использовался |
| `pyinstaller>=6.3` | EXE-сборка | Windows one-file | cx_Freeze не использовался |
| `argostranslate` (опционально) | Офлайн-перевод | При установке — приоритет над deep-translator | Часто не ставится на Python 3.13 |
| `gunicorn` | В `requirements.txt` | Для server deploy | Desktop использует Flask dev server |

---

### 4. Какие ошибки были исправлены

#### 65% TTS hang
- **Причина:** шаг `tts` стартует на **65%** (`_set_step(..., "tts", 65.0)`). Зависание — Edge-TTS без timeout, блокирующий/event loop, отсутствие per-segment progress update при сбое.
- **Исправление:** `engines/tts.py` — `asyncio.wait_for(..., timeout=120)`, retry ×3; `auto_dub_api.py` — цикл TTS с обновлением progress 65–80%, `TimeoutError` → понятная ошибка `tts_timeout`; `_ensure_control` помечает задачу `error` вместо «вечного running».

#### split_by_timing_map mismatch
- **Причина:** перевод шёл одним блоком → 1 строка vs N сегментов Whisper → `ValueError`.
- **Исправление:** `translate_segments()` + `align_segments_to_timing_map()` + recovery в `split_by_timing_map` (auto-split одного блока, padding пустых, merge лишних). Unit-тест: `scripts/test_segment_alignment.py`.

#### dub.html errors
- **Было:** файл содержал git diff, не HTML.
- **Стало:** полноценный `templates/dub.html` + `static/js/dub.js` (drop-zone, языки, прогресс, download, save to folder).

#### VS Code errors
- **Было:** неверный синтаксис в JS (`def applyLicenseBanner`), возможные lint на битых шаблонах.
- **Стало:** `license.js:26` — `function applyLicenseBanner`; `.vscode/settings.json` — `python-envs.defaultEnvManager: system`. Полный lint-проход в среде агента **не выполнялся**.

#### pipeline problems
- Язык «Авто» больше не ломает перевод (`source_lang: null` → detected_lang).
- Убраны debug `print`, дубли `_fail()`, whitelist моделей Whisper.
- FFmpeg extract с subprocess timeout 300 с + fallback через ffmpeg-python.
- DubEngine: progress callback, timeout по длительности видео.
- E2E: синус/тишина заменены на речь Edge-TTS (`scripts/e2e_test.py`).

#### auto-dub problems
- Лицензия: `require_feature("auto_dub")` на `/api/auto_dub/start`.
- Поиск видео: uploads / output / absolute path.
- Локализованные сообщения об ошибках (ru/en/uk).
- Preview сегментов в status API.

#### re-run problems
- `localStorage.vm_active_task` + `tryResumeTask()` — восстановление polling после перезагрузки страницы.
- `/api/auto_dub/resume/<task_id>` — сброс paused/editing после интерактивной паузы.
- **Ограничение:** повторный старт нового дубляжа при «зависшей» задаче на сервере не отменяет старый thread явно; UI блокирует кнопку только через `state.running`. После error/done — `localStorage` очищается.

---

### 5. Какие решения были приняты

| Решение | Альтернативы | Почему текущее |
|---------|--------------|----------------|
| Посегментный перевод + alignment | Перевод блока + split post-hoc | Меньше mismatch; split — только fallback |
| Edge-TTS + deep-translator online | Полностью офлайн (Piper + Argos) | Быстрее внедрить; качество голосов выше |
| Flask + Jinja + vanilla JS | React SPA в `src/` | Уже работающий стек; React scaffold не подключён |
| pywebview desktop | Electron/Tauri | Меньший размер, Python-native |
| Demo 7 дней → Basic (не удалять app) | Hard lock / uninstall | UX для тестеров; upsell premium |
| Тестовые ZIP вместо обязательного EXE | Только PyInstaller | Проще для итераций; EXE — опционально `build_windows.bat` |
| Owner secret в `data/license_secret.txt` | Только env | Авто-init при first run |
| `_fail` не убивает задачу в режиме `editing` | Всегда terminal error | Интерактивное редактирование сегментов (Pro) |

---

### 6. Что осталось нерешённым

#### Выполнено (subagent ad254a01 — код на месте, статический обзор)

1. **Subtitle suite MVP:** `engines/subtitle_formats.py` — parse/export SRT, VTT, ASS, SSA, TXT; `api/studio_api.py` — import, export SRT/VTT, `prepare_redub` + `GET /api/studio/redub/<id>`; `studio.html` — accept `.srt,.vtt,.ass,.ssa,.txt`, кнопки export; `studio.js` — `exportSubs`, redub → `/dub?redub=`.
2. **Universal import (сквозной поток):** `vmConsumeUniversalImport()` в `main.js` — `?import=` + fallback `sessionStorage.vm_import_file`; потребители: `dub.js`, `voice.html`, `studio.js`; meta оригинального имени в `uploads/imports/*.meta.json`.
3. **Voice audio STT (Task C MVP):** `/voice` — upload → STT → edit → TTS → export TXT/MP3; `_resolve_audio_path()` ищет файлы в `uploads/` и `uploads/imports/`.
4. **Ecosystem:** `scripts/build_ecosystem.bat` (7 entry points, hiddenimports), `install_ecosystem.bat` (корень), `engines/ecosystem_installer.py`, hook в `owner_first_run.py` → `%LOCALAPPDATA%\VideoMonsterFreeApps`.

#### Осталось нерешённым

1. **`dist/ecosystem/` пуст/отсутствует** — `build_ecosystem.bat` **не запускался** в среде агента; 7 EXE требуют **локального PyInstaller** на Windows ПК владельца (`pip install pyinstaller` → `scripts\build_ecosystem.bat`).
2. **Runtime-тесты не выполнялись:** shell в Cursor sandbox не прогнал E2E/smoke; нет `output/master_check_results.txt`, нет подтверждения MP4, subtitle export/redub, voice STT, universal import и ecosystem install на целевой машине.
3. **`data/test_builds_registry.json` пуст** — first-run init на этой копии, видимо, не создавал TEST-7 ZIP (маркер `data/.owner_initialized` может быть отдельно).
4. **Полностью офлайн дубляж** невозможен (Edge-TTS + deep-translator требуют сеть).
5. **React/Vite часть** — мёртвый код, путаница для архитектора.
6. **Долгие видео / large Whisper** — не профилировались; риск RAM/timeout на TTS×N сегментов.

---

### 7. Тестирование

| Сценарий | Статус | Комментарий |
|----------|--------|-------------|
| Короткое видео | ⚠️ Не прогонялось агентом | `scripts/e2e_test.py` создаёт 4 с MP4 с речью — **запустить локально** |
| Длинное видео | ❌ Не тестировалось | Нет автотеста |
| Разные языки | ⚠️ Статически OK | `translate_segments`, `LANGUAGES`, голоса в `data/languages.py` |
| Разные голоса | ⚠️ Статически OK | Edge-TTS voice id из UI |
| Без интернета | ⚠️ Частично | Whisper/Argos офлайн; TTS и deep-translator **упадут** — сообщение `tts_timeout` / translate fallback |
| Создание MP4 | ⚠️ Не прогонялось | Пайплайн до DubEngine реализован; нужен FFmpeg локально |
| 7-day test build | ⚠️ Код готов | `create_test_build("TEST-7")` — registry пуст, ZIP не создавался на этой машине в агенте |
| `python -c "import app"` | ❌ Shell blocked | Команда вернула пустой вывод в Cursor sandbox |
| `test_segment_alignment.py` | ❌ Не запущен | Скрипт существует, логика простая |
| `run_smoke_test.bat` | ❌ Не запущен | Ожидает локальный Windows + Python + FFmpeg |
| Universal import (`?import=` → dub/studio/voice) | ⚠️ Код готов | `import_api` + `main.js` + page loaders; **не прогонялось** end-to-end |
| Subtitle import/export/redub | ⚠️ Код готов | `subtitle_formats.py`, `studio_api`, `studio.js`; **не прогонялось** |
| Voice audio → STT → TTS | ⚠️ Код готов | `voice_api`, `voice.html`; Whisper + FFmpeg **не проверялись** в runtime |
| Ecosystem EXE (`build_ecosystem.bat`) | ❌ Не запускался | Скрипт и `ecosystem_installer.py` на месте; `dist/ecosystem/` **пуст** — нужен локальный PyInstaller |
| `install_ecosystem.bat` | ❌ Не запускался | Копирует EXE в `%LOCALAPPDATA%\VideoMonsterFreeApps` после сборки |

**Честный итог:** ad254a01 закрыл **код** subtitle suite, universal import, voice STT и ecosystem scaffold, но вся верификация в сессии агентов остаётся **статической**. Единственный надёжный путь — `run_smoke_test.bat` + локально `scripts\build_ecosystem.bat` на ПК пользователя.

---

### 8. ОЦЕНКА КОДА (1–10)

| Критерий | Оценка | Слабые места |
|----------|--------|--------------|
| **Стабильность** | 6/10 | Критические баги и ad254a01-фичи закрыты в коде, но **нет runtime-подтверждения**; TTS/сеть — SPOF |
| **Архитектура** | 7/10 | Blueprints + engines; `subtitle_formats`, `ecosystem_installer`, import upload/load; глобальные `AUTO_TASKS`, мёртвый React |
| **UX** | 8/10 | `/dub` «одна кнопка», universal import, studio export/redub, voice audio STT; polish studio/voice — средний |
| **Производительность** | 5/10 | Посегментный TTS последовательный; large Whisper на длинном видео — риск |
| **Расширяемость** | 8/10 | Engines отделены; subtitle/ecosystem модули; license features map; apps/* для экосистемы |
| **UI quality** | 7/10 | Единый CSS, responsive sidebar; studio/voice расширены ad254a01, но не polish |

---

### 9. ОБЪЯСНЕНИЕ ДЛЯ ЧЕЛОВЕКА

**Что изменилось для обычного пользователя (2–3 минуты чтения):**

VideoMonster V2 — программа на Windows, которая берёт ваше видео и делает переведённую озвучку в новом MP4-файле. Раньше главный экран «Авто-дубляж» был сломан, а процесс часто застревал на середине (около 65%) и не выдавал готовое видео.

Сейчас вы открываете программу двойным щелчком по `install_and_run.bat` (нужны Python и FFmpeg), попадаете сразу на страницу дубляжа, перетаскиваете видео, выбираете язык перевода и голос, нажимаете одну кнопку. Программа сама: вытаскивает звук, распознаёт речь, переводит, озвучивает и собирает новое видео. В конце можно скачать MP4 или сохранить в папку.

Есть простой и «про» режим: в простом скрыты лишние настройки. Есть пробный период около 7 дней с полным доступом; потом остаются базовые функции, а авто-дубляж просит ключ Premium.

Что ещё не идеально: для озвучки нужен интернет; отдельные мини-программы (Reader, Studio и т.д.) как отдельные EXE ещё не собраны автоматически; загрузка субтитров VTT и перезапуск дубляжа из студии — в процессе доработки. Владельцу программы доступна панель в «Настройках» для создания тестовых ZIP-архивов с ключом на 7 или 30 дней.

---

### 10. ФИНАЛЬНОЕ ЗАКЛЮЧЕНИЕ

| Вопрос | Ответ |
|--------|-------|
| **Готов для обычных пользователей?** | **Условно да** — после локального `run_smoke_test.bat`, установки FFmpeg, pip-зависимостей и понимания, что нужен интернет для озвучки. Код ad254a01 (studio/voice/import) на месте, но **без runtime-теста** — **не рекомендовать как 100% stable**. |
| **Что ещё улучшить?** | (1) Прогнать E2E и smoke локально (import, studio export/redub, voice STT); (2) собрать ecosystem EXE: `pip install pyinstaller` → `scripts\build_ecosystem.bat` → `scripts\install_ecosystem.bat`; (3) убрать или интегрировать React scaffold; (4) профиль длинного видео. |
| **Рекомендовать как main build?** | **Да как основную ветку V2 для desktop/ZIP-раздачи**, если владелец подтвердит E2E, MP4 и хотя бы один сценарий studio/voice на своём ПК. **Нет** как «production без проверки», **нет** для полностью офлайн-сценария, **нет** для ecosystem EXE без локальной PyInstaller-сборки. |

---

### 11. POST-REAL-TEST FIXES (16 июня 2026)

| Область | Корневая причина | Исправление |
|---------|------------------|-------------|
| **Два голоса** | UI и API по умолчанию `dub_mode=mix`, `mix_volume=0.3` — amix накладывал оригинал на дубляж | `mix_mode=full_dub` по умолчанию; пресеты `full_dub/atmosphere/language_learning/custom`; FFmpeg replace при orig=0% |
| **Экосистема** | `install_ecosystem(build_if_missing=False)` при first-run; EXE не собирались | `build_if_missing=True`, ярлыки на рабочий стол, кнопка «Создать экосистему» |
| **TEST-7 EXE** | `create_test_build` делал только ZIP; PyInstaller не вызывался | `create_test_exe_build()` → `VideoMonster_Test_7_Days.exe`; кнопка в панели владельца |
| **First-run** | `is_owner_host()` требовал `license_secret.txt` до init; ошибки глотались | Расширена логика owner-копии; exception logging в `run_if_needed` |
| **Оригинал в проекте** | extracted MP3 удалялся после экспорта | Чекбокс `keep_original_track` → копия в `projects/` |

**Новые/изменённые файлы:** `engines/dub_engine.py`, `api/auto_dub_api.py`, `api/dub_api.py`, `templates/dub.html`, `static/js/dub.js`, `engines/owner_first_run.py`, `engines/ecosystem_installer.py`, `engines/test_build_manager.py`, `engines/owner_build_jobs.py`, `api/owner_api.py`, `templates/settings.html`, i18n JSON.

**Runtime §7:** `python -c "import app"` — см. результат агента; PyInstaller-сборки EXE/ecosystem **не прогонялись** в CI (требуют локально 5–30 мин и `pip install pyinstaller`).

---

### 12. TUBEDUB REBRAND (16 июня 2026)

| Область | Было | Стало |
|---------|------|-------|
| **UI / окно** | VideoMonster V2 | **TubeDub** + подпись *Powered by VideoMonster Engine* |
| **Тест EXE** | `VideoMonster_Test_7_Days.exe` | **`TubeDub_Test_7_Days.exe`** |
| **Экосистема (ярлыки)** | VM Reader, VM Озвучка… | **TubeDub Reader**, TubeDub Озвучка… (EXE в `%LOCALAPPDATA%\VideoMonsterFreeApps` без переименования) |
| **Внутреннее** | Папка `VideoMonster_V2`, ключи `VM-`, API routes | **Без изменений** |

**Качество (post-test):** batch-перевод по умолчанию через `translation_naturalizer`; блок `_OUTPUT_` на API; TTS merge <2 с + Edge-TTS rate; full_dub `-map -0:a`; redub `skip_translate` только явно из Студии.

**Файлы:** `templates/base.html`, `dub.html`, `settings.html`, `voice.html`, `studio.html`, `desktop.py`, `README.md`, `install_and_run.bat`, `static/i18n/*.json`, `engines/test_build_manager.py`, `api/owner_api.py`, `engines/ecosystem_installer.py`, `api/auto_dub_api.py`, `engines/translation_naturalizer.py`, `engines/tts.py`, `TRANSLATION_QUALITY.md`.

---

### 13. SEGMENTATION STABILITY (дополнение №2, 16 июня 2026)

| Проблема | Диагноз | Исправление |
|----------|---------|-------------|
| Роботизированные вставки | `atempo=2.0` в `dub_timing_fit_log.txt` на коротких Whisper-слотах | STT merge (`segment_merger.py`), TTS min 4.5 с, atempo max 1.30 |
| Нестабильное качество | Часть слотов `atempo=1.0`, часть `2.0` | Пропорциональный `split_by_timing_map`, без обрезки TTS |
| «Рывки» видео | `-shortest` убран ранее; stutter = аудио squeeze | `-t` + `apad` на dub/mix; `-c:v copy` |
| Перескоки кадров | Нет re-encode видео | FPS сохраняется (copy) |
| Mix 30–40% лучше | Полевой тест | Default `language_learning` 38% |

**Новые файлы:** `engines/segment_merger.py`, `scripts/audit_segmentation.py`  
**Изменены:** `engines/timing_fit.py`, `engines/cleaner.py`, `api/auto_dub_api.py`, `engines/dub_engine.py`, `templates/dub.html`, i18n.

**Readiness:** unit-тесты OK; полный E2E на реальном MP4 — повторить локально (`output/tubedub_quality_report.txt`).

---

*Документ подготовлен для handoff второму архитектору. Обновлять после локального `run_smoke_test.bat` и завершения subagent §17.*
