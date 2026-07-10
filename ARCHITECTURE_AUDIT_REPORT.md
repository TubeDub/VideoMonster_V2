# ПОЛНЫЙ АРХИТЕКТУРНЫЙ АУДИТ TUBEDUB 1.0
# Full Architecture Audit Report

**Роль:** Senior Software Architect / Principal Backend Architect  
**Дата:** 25.06.2026  
**Статус:** READ-ONLY — ни одна строка кода не изменена  
**Методология:** Три параллельных независимых агента сканировали:
  - API-слой, инвентаризацию, feature flags, i18n
  - Все 25+ модулей engines/, граф зависимостей, дублирование
  - Шаблоны, тесты, технический долг, зависимости, логирование

---

## ЧАСТЬ 1. ПОЛНАЯ КАРТА АРХИТЕКТУРЫ

### 1.1 Общая структура проекта

```
VideoMonster_V2/
├── app.py                    ← Flask entrypoint, page routes, module guard
├── desktop.py                ← PyWebView desktop wrapper
├── wsgi.py                   ← Production WSGI
├── license_server.py         ← Standalone license server (отдельный процесс)
│
├── api/                      ← 30 Blueprint-модулей (API слой)
│   ├── auto_dub_api.py       ← ГЛАВНЫЙ: пайплайн дубляжа (~4975 строк)
│   ├── studio_api.py         ← Post-dub студия (1788 строк)
│   ├── translate_api.py      ← Переводчик
│   ├── reader_api.py         ← Книги / VMR
│   ├── dub_api.py            ← Загрузка/скачивание видео
│   ├── tts_api.py            ← TTS endpoints
│   └── ... (25 других API)
│
├── engines/                  ← 321 файл, ~245 активных .py
│   ├── stt_engine.py         ← Whisper STT
│   ├── tts.py                ← Edge-TTS синтез
│   ├── timing_fit.py         ← Подгонка аудио к таймингу
│   ├── dub_engine.py         ← FFmpeg видео-микс
│   ├── translation_naturalizer.py  ← Натурализация перевода
│   ├── adaptive_dubbing_adapter.py ← Pre-TTS адаптация текста
│   ├── soft_sync.py          ← Мягкое растяжение/сжатие
│   ├── stress_marks.py       ← Ударения UK/RU
│   ├── pipeline_cache.py     ← Дисковый кеш Whisper/перевода
│   ├── semantic_adaptation.py      ← Семантическое укорочение
│   ├── translation_adapt.py        ← Укорочение по ступеням
│   ├── dubbing_engine/             ← Единый 7-этапный движок
│   │   ├── engine.py         ← DubbingEngine (оркестратор)
│   │   ├── entities.py       ← Stage 1: Named entities
│   │   ├── punctuation.py    ← Stage 3: Пунктуация
│   │   ├── predictor.py      ← Stage: Предиктор длительности (слоги)
│   │   ├── phonetics.py      ← Stage 4.5: Фонетика (Fiat→Фіат)
│   │   ├── validation.py     ← Stage 7: Валидация перед TTS
│   │   ├── content_mode.py   ← Профили контента (movie/podcast/…)
│   │   ├── project_session.py ← Изоляция сессий
│   │   ├── segment_log.py    ← 12-полевой TSV лог
│   │   └── types.py          ← Shared datatypes
│   └── smart_segment_optimizer/   ← SSO: правила сжатия L1-L5
│
├── templates/                ← 21 HTML-страница (Jinja2)
├── static/                   ← 35 файлов (21 JS, 4 i18n, CSS, иконки)
├── src/                      ← React/shadcn UI kit (Vite) — параллельный стек
├── data/                     ← JSON конфиги: feature_flags, modules, voices
├── tests/                    ← 17 тест-модулей, ~191 кейс
├── scripts/                  ← 73 скрипта (dev/audit/e2e)
├── apps/                     ← 7 standalone mini-apps
└── tools/ffmpeg/             ← Встроенный FFmpeg/FFprobe
```

### 1.2 Подсистемы

| Подсистема | Файлы | Назначение | Владелец данных |
|-----------|-------|-----------|----------------|
| **GUI** | `app.py`, `templates/*`, `static/js/*` | Страницы, навигация | — |
| **API** | `api/*.py` | HTTP-маршруты, Blueprint | AUTO_TASKS (задачи дубляжа) |
| **AutoDub Pipeline** | `api/auto_dub_api.py` + `engines/*` | Полный дубляж видео | auto_dub_api.AUTO_TASKS |
| **Translation** | `api/translate_api.py`, `engines/mt/*`, `engines/enterprise_translation/*` | Перевод текста/файлов/видео | per-request, без глобального состояния |
| **Reader** | `api/reader_api.py`, `templates/reader.html` | Чтение VMR-книг | per-request |
| **Studio** | `api/studio_api.py` | Редактирование дубляжа | AUTO_TASKS (читает из auto_dub_api) |
| **Voice/TTS** | `api/tts_api.py`, `engines/tts.py`, `engines/tts_engines/*` | Синтез речи | output/ (файлы) |
| **STT** | `engines/stt_engine.py` | Распознавание речи (Whisper) | `_MODEL_CACHE` (singleton) |
| **DubbingEngine** | `engines/dubbing_engine/*` | 7-этапный pre-TTS оркестратор | per-call, instance-based |
| **Timing** | `engines/timing_fit.py`, `engines/soft_sync.py` | Аудио-слоты, темп | per-call, OS tempdir |
| **Cache** | `engines/pipeline_cache.py` | Диск-кеш Whisper/перевода | `output/cache/pipeline/` |
| **Config** | `data/feature_flags.json`, `data/module_registry.json` | Флаги, реестр модулей | JSON-файлы |
| **Localization** | `static/i18n/*.json`, `engines/locale_utils.py` | UI-строки (4 языка) | JSON-файлы |
| **Licensing** | `engines/license_manager.py`, `api/license_api.py` | Проверка лицензии | `license.json` |
| **Model Manager** | `engines/model_manager/*`, `api/models_api.py` | Загрузка ML-моделей | `data/model_manager.json`, `models/` |
| **Project Session** | `engines/dubbing_engine/project_session.py` | Изоляция между задачами | `_SESSIONS` (global dict) |
| **Platform/Cloud** | `api/platform_api.py`, `api/cloud_api.py`, `engines/cloud/*` | Расширенные функции (off) | Feature-flagged OFF |
| **Diagnostics** | `api/dev_api.py`, `engines/translation_quality_log.py`, `engines/tts_text_path.py` | Dev-логи, трейсы | `output/dev/` |

---

## ЧАСТЬ 2. КАРТА ЗАВИСИМОСТЕЙ

### 2.1 Граф модульных зависимостей (ключевые связи)

```
app.py
  └── engines/app_loader.py (lazy blueprint load)
        ├── api/auto_dub_api.py ←→ api/studio_api.py  [CIRCULAR, lazy, mitigated]
        ├── api/translate_api.py
        ├── api/reader_api.py
        └── api/tts_api.py

api/auto_dub_api.py
  ├── engines/stt_engine.py
  ├── engines/tts.py
  │     └── engines/stress_marks.py  [@lru_cache]
  ├── engines/timing_fit.py
  │     └── engines/soft_sync.py (feature-flagged)
  ├── engines/dub_engine.py
  ├── engines/translation_naturalizer.py
  │     └── engines/soft_sync.py (lazy)
  ├── engines/dubbing_engine/engine.py  ← PRIMARY PATH
  │     ├── engines/dubbing_engine/entities.py → types.py
  │     ├── engines/dubbing_engine/predictor.py
  │     ├── engines/dubbing_engine/punctuation.py
  │     ├── engines/dubbing_engine/phonetics.py
  │     ├── engines/dubbing_engine/validation.py → entities.py
  │     ├── engines/dubbing_engine/content_mode.py
  │     ├── engines/adaptive_dubbing_adapter.py
  │     │     └── engines/dubbing_engine/predictor.py
  │     │     └── engines/dubbing_engine/phonetics.py (lazy)
  │     ├── engines/smart_segment_optimizer/optimizer.py
  │     │     └── engines/semantic_adaptation.py
  │     │           └── engines/translation_adapt.py
  │     └── engines/stress_marks.py
  ├── engines/pipeline_cache.py
  └── engines/semantic_adaptation.py (legacy path)

api/studio_api.py
  └── api/auto_dub_api.py (lazy: AUTO_TASKS, STATE_LOCK, builders)
```

### 2.2 Сильная связанность (High Coupling)

| Пара | Тип связи | Риск |
|------|-----------|------|
| `auto_dub_api` ↔ `studio_api` | Двусторонняя, lazy import | СРЕДНИЙ: безопасно при загрузке, но тесная runtime-связь |
| `DubbingEngine` → `ADA` + `SSO` + `translation_adapt` | Цепочка вызовов | СРЕДНИЙ: три параллельных механизма укорочения |
| `timing_fit` → `soft_sync` | Feature-flag зависимость | НИЗКИЙ: feature off = не активно |
| `auto_dub_api` → `engines/*` | 50+ lazy импортов | СРЕДНИЙ: загрузка по требованию, но масштабируемость снижена |

### 2.3 Дублирование функциональности (Overlap)

| Функция | Реализации | Файлы |
|---------|-----------|-------|
| **Укорочение текста** | 7 реализаций | `ADA`, `SSO`, `translation_adapt`, `semantic_adaptation`, `soft_sync`, `translation_naturalizer.shorten_for_slot`, `DubbingEngine._stage_adapt` |
| **Предсказание длительности** | 3 реализации | `predictor.predict_ms` (слоги), `semantic_adaptation.estimate_tts_duration_ms` (символы), `DubbingEngine._predict_ms` (fallback chars) |
| **Пост-предложенческие паузы** | 3 отдельные таблицы `_PUNCT_PAUSE_MS` | `timing_fit.py`, `adaptive_dubbing_adapter.py`, `dubbing_engine/punctuation.py` |
| **`_normalize_lang()`** | 5 копий | `translation_naturalizer`, `dub_style_loader`, `semantic_translation`, `translation_pipeline`, `semantic_adaptation` |
| **TTS генерация** | 3 точки входа | `engines/tts.py` (основной), `engines/soft_sync.py` (retry), `engines/dub_studio/tts_regen.py` (студия) |

---

## ЧАСТЬ 3. КАРТА ПОТОКОВ ДАННЫХ

### 3.1 Полный поток AutoDub

```
[Пользователь загружает видео]
        │
        ▼
POST /api/dub/upload_video
  → Файл сохраняется в uploads/{uuid}.mp4
  → state.filename передаётся на фронтенд
        │
        ▼
POST /api/auto_dub/start
  Создаёт:  task_id = uuid4().hex
  Создаёт:  AUTO_TASKS[task_id] = {status, progress, info:{}}
  Запускает: threading.Thread → _run_pipeline()
        │
        ▼
[ЭТАП 1: STT / Whisper]
  Входит:  video_path
  Создаёт: extracted_audio → output/{base_id}_extracted.mp3
  Создаёт: raw_lines[], timing_map[] (list of {start, end, text})
  Сохраняет в AUTO_TASKS[task_id]["info"]:
    source_segments[]      (copy.deepcopy)
    timing_map_backup[]    (copy.deepcopy)
  Читает:  _MODEL_CACHE[model_size]  ← GLOBAL SINGLETON
        │
        ▼
[ЭТАП 2: PIPELINE CACHE CHECK]
  Входит:  video fingerprint
  Читает:  output/cache/pipeline/{fingerprint}.json
  Если hit → пропускает Whisper и Translation
        │
        ▼
[ЭТАП 3: TRANSLATION]
  Входит:  raw_lines[] (EN)
  Создаёт: segments[] (list[str], целевой язык)
  Сохраняет: translation_audits[] в AUTO_TASKS[task_id]["info"]
  Пишет:  output/dev/translation_quality.log  (APPEND, SHARED)
        │
        ▼
[ЭТАП 4: DubbingEngine.process_all()]  ← ГЛАВНЫЙ PRE-TTS
  Входит:  segments[], timing_map[], source_hints[]
  Stage 1: entities    → EntityInfo[]
  Stage 2: adapt       → SSO → ADA → translation_adapt
  Stage 3: punctuation → restore_punctuation()
  Stage 4: stress      → add_stress_marks()
  Stage 4.5: phonetics → resolve_phonetics() (Fiat→Фіат)
  Stage 5: voice_gate  → re-adapt if atempo > threshold
  Stage 6: timing      → strategy: direct/video_adapt/merge_next/overlap_blocked
  Stage 7: validation  → run_validation() → 8 checks
  Создаёт: DubbingResult[] (новые объекты)
  Пишет:  output/dev/engine_{run_id}.json
  Пишет:  output/dev/engine_latest.json  ← ПЕРЕЗАПИСЫВАЕТСЯ
        │
        ▼
[ЭТАП 5: TTS GENERATION]
  Входит:  adapted text per segment, voice, lang
  Создаёт: output/{task_id[:8]}_seg{i:04d}.mp3
  Метод:  asyncio.gather() + Semaphore (параллельно)
  Сохраняет пути в: AUTO_TASKS[task_id]["info"]["segments_data"]
        │
        ▼
[ЭТАП 6: TIMING FIT]
  Входит:  TTS MP3 paths[], timing_map[]
  Создаёт: tempfile.mkdtemp("timing_fit_")  ← OS UUID
  Создаёт: timed audio track
  Очищает: finally: rmtree(work_dir)  ✓
  Пишет:  output/dub_timing_fit_log.txt  (APPEND, SHARED)
        │
        ▼
[ЭТАП 7: DubEngine / FFmpeg MIX]
  Входит:  video_path, timed_audio, timing_map
  Создаёт: output/{video_stem}_OUTPUT_{base_id}.mp4
  Применяет: loudnorm, ducking, video setpts stretch
        │
        ▼
[ЗАВЕРШЕНИЕ: studio_ready]
  AUTO_TASKS[task_id]["status"] = "studio_ready"
  publish_studio_ready(task_id)  ← вызывает studio_api
  Pipeline thread завершается
  AUTO_TASK_CONTROLS.pop(task_id)  ✓
  AUTO_TASKS[task_id] — НЕ удаляется  ✗
        │
        ▼
[STUDIO PHASE (асинхронно, по запросу)]
POST /api/studio/mix/<task_id>
  Читает: AUTO_TASKS[task_id]  (может читать часами позже)
  Создаёт: финальный MP4
        │
        ▼
[ЭКСПОРТ]
POST /api/dub/save_to_folder
  Копирует в: user-chosen path
```

### 3.2 Поток перевода (Translation module)

```
[Файл/текст/видео/аудио/SRT]
        │
        ▼
POST /api/translate/pipeline
  → STT (если аудио/видео) → MT → naturalize → output
  → Stateless: нет глобального task registry
  → Файлы: output/{uuid}_translated.* (UUID-scoped)
```

### 3.3 Поток Reader

```
[VMR / EPUB / PDF / DOCX]
        │
        ▼
POST /api/load_vmr         → читает JSON проект
POST /api/reader/import    → парсит book format
POST /api/tts              → TTS для текущей страницы
GET  /api/reader/document/ → отдаёт страницу
```

---

## ЧАСТЬ 4. КАРТА ЖИЗНЕННОГО ЦИКЛА ОБЪЕКТОВ

### 4.1 Главные объекты и их жизненный цикл

| Объект | Создаётся | Изменяется | Уничтожается | Риск |
|--------|-----------|-----------|-------------|------|
| `AUTO_TASKS[task_id]` | POST /start | Весь pipeline, studio | **НИКОГДА** | 🔴 КРИТИЧНО |
| `AUTO_TASK_CONTROLS[task_id]` | POST /start | Pipeline | finally (done/error) | ✓ |
| `_MODEL_CACHE[size]` (Whisper) | Первый вызов transcribe() | Нет | Process end | 🟡 Intentional |
| `_SESSIONS[task_id]` (ProjectSession) | create_session() | finish_session() | TTL 1ч или cleanup_session() | 🟡 |
| `segments[]` | Translation | DubbingEngine (in-place!) | GC после pipeline | 🟡 |
| `timing_map[]` | STT | deepcopy → backup | GC | ✓ deepcopy |
| `DubbingEngine` instance | process_all() call | 7 stages | GC после return | ✓ |
| `DubEngine` instance | mix() call | run() | GC после return | ✓ |
| TTS MP3 files | generate_audio() | Нет | unlink() после mix (условно) | 🟡 |
| timing_fit tempdir | mkdtemp() | ffmpeg | finally: rmtree() | ✓ |
| `output/{base_id}_extracted.mp3` | FFmpeg extract | Нет | unlink() (условно) | 🟡 |
| `output/*_OUTPUT_{base_id}.mp4` | DubEngine.run() | Нет | Никогда (user output) | ✓ |
| `lru_cache` (stress_marks) | Module import | По вызовам | Process end | 🟢 |

### 4.2 Остаётся ли хоть один объект после завершения проекта?

**Да.** Подтверждено кодом:

1. **`AUTO_TASKS[task_id]`** — остаётся в памяти навсегда. Содержит: `segments_data[]`, `source_segments[]`, `timing_map_backup[]`, `translation_audits[]`. `api/auto_dub_api.py:4885-4930` — только `AUTO_TASK_CONTROLS.pop()`.

2. **Whisper `_MODEL_CACHE`** — намеренно, singleton. `engines/stt_engine.py:24`.

3. **`_SESSIONS[task_id]`** — остаётся до TTL (1 ч) или следующего `create_session()`. `engines/dubbing_engine/project_session.py:26`.

4. **TTS MP3 файлы** — при пути через `studio_ready` pipeline завершается до блока cleanup (строки 4769-4785). `engines/tts.cleanup_old_files()` вызывается только из `tts_api`, не из `auto_dub`.

---

## ЧАСТЬ 5. СПИСОК АРХИТЕКТУРНЫХ НАРУШЕНИЙ

### Критические нарушения (🔴)

---

**[V-01] AUTO_TASKS не эвикируется — бесконечное накопление в памяти**

- **Файл:** `api/auto_dub_api.py`
- **Строки:** 32, 4885–4930
- **Механизм:** `AUTO_TASKS = {}` — module-level dict. Добавляется запись при каждом `POST /api/auto_dub/start`. В `finally` блоке удаляется только `AUTO_TASK_CONTROLS[task_id]`. `AUTO_TASKS[task_id]` с полным набором сегментов, переводов и аудиопутей остаётся навсегда.
- **Последствия:** Рост памяти пропорционально числу дубляжей. Старый `task_id` доступен через studio API. При 50+ запусках — 50 полных наборов данных в RAM.
- **Уверенность:** **Высокая** — подтверждено кодом

---

**[V-02] ProjectSession создан, но не применяется для изоляции путей**

- **Файл:** `api/auto_dub_api.py` (строки 3015–3027), `engines/dubbing_engine/project_session.py` (строки 59, 97–123)
- **Механизм:** `create_session()` создаёт `ProjectSession` с `session_dir = output/sessions/{task_id}/`. Объект `_session` присваивается. Но ни в одном месте пайплайна не используется `_session.session_dir` для путей TTS/extract/timed-файлов. Все пути строятся через общий `OUTPUT_DIR`.
- **Последствия:** Архитектурная граница изоляции объявлена, но не применяется. Реальная изоляция — только UUID-префиксы в именах файлов в общей папке.
- **Уверенность:** **Высокая** — подтверждено кодом

---

**[V-03] Двусторонняя зависимость auto_dub_api ↔ studio_api**

- **Файлы:** `api/auto_dub_api.py:842`, `api/studio_api.py:73-78,157,289`
- **Механизм:** `studio_api` импортирует `AUTO_TASKS`, `STATE_LOCK`, `_build_timed_dub_track`, `_safe_export_audio` из `auto_dub_api` внутри функций. `auto_dub_api` импортирует `publish_studio_ready` из `studio_api` внутри функций.
- **Последствия:** Нет единого владельца данных задачи. Цикличная зависимость, безопасная при lazy-загрузке, но архитектурно неправильная. Изменение в одном модуле ломает другой.
- **Уверенность:** **Высокая** — подтверждено кодом

---

### Высокие нарушения (🟠)

---

**[V-04] Три параллельных предиктора длительности с разными моделями**

- **Файлы:** `engines/dubbing_engine/predictor.py:121` (слоги), `engines/semantic_adaptation.py:123` (символы/сек), `engines/dubbing_engine/engine.py:42-50` (chars fallback)
- **Механизм:** `predict_ms()` (слоговый) и `estimate_tts_duration_ms()` (символьный) дают разные результаты для одного текста. `DubbingEngine._stage_adapt` использует `predict_ms`, SSO/soft_sync используют `estimate_tts_duration_ms`.
- **Последствия:** Непоследовательные решения PASS/FIT в одном и том же пайплайне. Текст может проходить проверку в одном месте и не проходить в другом.
- **Уверенность:** **Высокая** — подтверждено кодом

---

**[V-05] Семь параллельных систем укорочения текста**

- **Файлы:** `engines/adaptive_dubbing_adapter.py`, `engines/smart_segment_optimizer/optimizer.py`, `engines/translation_adapt.py`, `engines/semantic_adaptation.py`, `engines/soft_sync.py`, `engines/translation_naturalizer.py:1057`, `engines/dubbing_engine/engine.py:318-413`
- **Механизм:** Каждый модуль реализует собственные правила укорочения, синонимы, защиту слов. Пересечение: SSO `levels.py:95` и ADA `_SYNONYMS` (строки 155+) содержат похожие, но разные таблицы.
- **Последствия:** Противоречивые решения. Невозможно предсказать какая стратегия сработает. Техдолг обслуживания — изменение в одном не синхронизируется с другими.
- **Уверенность:** **Высокая** — подтверждено кодом

---

**[V-06] TTS MP3 файлы не очищаются в auto-dub пути**

- **Файл:** `engines/tts.py:315-323`, `api/auto_dub_api.py:4393,4769-4785`
- **Механизм:** Pipeline переходит к `studio_ready` на строке 4393 и завершает поток. Блок cleanup (4769-4785) остаётся недостижимым. `cleanup_old_files()` вызывается только из `api/tts_api.py`, не из auto-dub пути.
- **Последствия:** TTS MP3-файлы накапливаются в `output/` при каждом дубляже без очистки.
- **Уверенность:** **Высокая** — подтверждено кодом

---

**[V-07] Три отдельные таблицы `_PUNCT_PAUSE_MS`**

- **Файлы:** `engines/timing_fit.py`, `engines/adaptive_dubbing_adapter.py`, `engines/dubbing_engine/punctuation.py`
- **Механизм:** Три независимых словаря с паузами для знаков препинания. Значения различаются: `timing_fit` `.=160ms`, `punctuation.py` своя логика.
- **Последствия:** Несогласованные паузы в зависимости от того, какой модуль вычисляет.
- **Уверенность:** **Высокая** — подтверждено кодом

---

**[V-08] `_normalize_lang()` скопирована в 5 файлов**

- **Файлы:** `engines/translation_naturalizer.py:779`, `engines/dub_style_loader.py:88`, `engines/semantic_translation.py:142`, `engines/translation_pipeline.py:33`, `engines/semantic_adaptation.py:111`
- **Механизм:** Идентичная или почти идентичная helper-функция без единого источника истины.
- **Последствия:** Несинхронные изменения. Баг в одной копии не исправляется в других.
- **Уверенность:** **Высокая** — подтверждено кодом

---

### Средние нарушения (🟡)

---

**[V-09] `_SESSIONS` реестр — cleanup_session() не вызывается в production**

- **Файл:** `engines/dubbing_engine/project_session.py:26-27,182-196`
- **Механизм:** `_SESSIONS: dict = {}` — глобальный реестр. `finish_session()` вызывается (строки 3064-3065 auto_dub_api). `cleanup_session()` нигде не вызывается в production пайплайне. TTL-эвикция (1 час) срабатывает только при следующем `create_session()`.
- **Уверенность:** **Высокая**

---

**[V-10] Фиксированные `*_latest.*` dev-артефакты перезаписываются**

- **Файлы:** `engines/dubbing_engine/engine.py:705-739`, `engines/adaptive_dubbing_adapter.py:1776-1777`
- **Механизм:** `engine_latest.json`, `engine_latest.txt`, `ada_segment_audit_latest.log` перезаписываются при каждом запуске.
- **Последствия:** При параллельных дубляжах — смешение данных разных сессий в одном файле.
- **Уверенность:** **Высокая**

---

**[V-11] Append-only общие логи смешивают данные всех сессий**

- **Файлы:** `api/auto_dub_api.py:427-428`, `engines/timing_fit.py:714-715`
- **Механизм:** `dub_segment_log.txt`, `dub_timing_fit_log.txt` — фиксированные пути, режим append, без разделителя сессий.
- **Уверенность:** **Высокая**

---

**[V-12] Параллельные дубляжи используют общий `output/`**

- **Файлы:** `engines/tts.py:35`, `api/auto_dub_api.py:22-24`
- **Механизм:** Несколько `threading.Thread` с pipeline могут работать одновременно. Все TTS MP3 пишутся в одну плоскую директорию. UUID-префиксы снижают риск коллизии, но не устраняют его архитектурно.
- **Уверенность:** **Средняя**

---

**[V-13] `segments` мутируется in-place в `DubbingEngine.process_all()`**

- **Файл:** `engines/dubbing_engine/engine.py`, `api/auto_dub_api.py:3694+`
- **Механизм:** `DubbingEngine.process_all(segments, timing_map, ...)` принимает `segments: list[str]` и возвращает `DubbingResult[]`, но внутренние этапы могут мутировать переданный список.
- **Уверенность:** **Средняя** — требует дополнительной проверки

---

**[V-14] Reader API разделён между двумя Blueprint-ами**

- **Файлы:** `api/translate_api.py:447`, `api/reader_api.py:26-69`
- **Механизм:** Загрузка документа в `translate_api.py`, VMR CRUD в `reader_api.py`, bridge в `translate_api.py:406`. Нет единой точки ответственности.
- **Уверенность:** **Высокая**

---

**[V-15] `pywebview` двойная версионная привязка**

- **Файлы:** `requirements_desktop.txt` (>=4.4.1), `pyproject.toml` (>=5.0)
- **Механизм:** Конфликтующие минимальные версии.
- **Уверенность:** **Высокая**

---

### Низкие нарушения (🟢)

---

**[V-16] `templates/pipeline_dev.html` — orphan template без маршрута**

- **Файл:** `templates/pipeline_dev.html`
- **Механизм:** Нет `render_template("pipeline_dev.html")` в `app.py`. Заменён `dev_pipeline.html` / `/dev/pipeline`.
- **Уверенность:** **Высокая**

---

**[V-17] Два стека UI без интеграции**

- **Файлы:** `templates/*` + `static/js/*` (Jinja2/Vanilla JS) vs `src/` (React/Vite/shadcn)
- **Механизм:** React shell в `src/` параллельно существует, но не подключён к основным страницам Flask.
- **Уверенность:** **Высокая**

---

**[V-18] i18n охватывает только `dub.html` полностью**

- **Файлы:** `templates/index.html`, `templates/settings.html`, `templates/translate.html`, `templates/voice.html`, `templates/projects.html`, `templates/reader.html`
- **Механизм:** Только `dub.html` имеет 68 ключей `data-i18n`. Все остальные страницы имеют 0-8 ключей и хардкодированный русский текст.
- **Уверенность:** **Высокая**

---

**[V-19] Логирование: два naming-конвенции в одном приложении**

- **Файлы:** Весь проект
- **Механизм:** Часть модулей: `logger = logging.getLogger("tubedub.module_name")`. Часть: `logger = logging.getLogger(__name__)`. В одном файловом хендлере — сложная фильтрация.
- **Уверенность:** **Высокая**

---

**[V-20] Все зависимости `requirements.txt` с `>=` без точного пина**

- **Файл:** `requirements.txt`
- **Механизм:** `flask>=3.0.0`, `edge-tts>=6.1.9` и т.д. — нет точных версий. Нет `requirements.lock`.
- **Последствия:** Нереродуцируемые сборки. Обновление dependency может сломать prod.
- **Уверенность:** **Высокая**

---

## ЧАСТЬ 6. ТЕХНИЧЕСКИЙ ДОЛГ

### КРИТИЧНО — исправить до версии 1.0

*Проблемы, которые могут привести к потере данных, нестабильности или сбоям*

| ID | Проблема | Файл | Влияние |
|----|---------|------|---------|
| V-01 | AUTO_TASKS не эвикируется — утечка памяти | auto_dub_api.py:32 | Нестабильность при длительной работе |
| V-06 | TTS MP3 не очищаются в auto-dub пути | tts.py:315-323 | Переполнение диска |
| V-04 | Три предиктора с разными моделями | predictor.py, semantic_adaptation.py | Непредсказуемые решения адаптации |
| V-15 | Конфликт версий pywebview | requirements_desktop.txt, pyproject.toml | Ошибка сборки десктоп-версии |

### ЖЕЛАТЕЛЬНО — исправить до версии 1.0

*Архитектурные недостатки, влияющие на сопровождение и качество*

| ID | Проблема | Файл | Влияние |
|----|---------|------|---------|
| V-02 | ProjectSession не применяется для путей файлов | project_session.py, auto_dub_api.py | Ложная гарантия изоляции |
| V-03 | Двусторонняя зависимость auto_dub↔studio | auto_dub_api.py, studio_api.py | Архитектурная хрупкость |
| V-08 | `_normalize_lang()` в 5 копиях | 5 файлов engines/ | Баги при несинхронных изменениях |
| V-09 | cleanup_session() не вызывается | project_session.py | Утечка сессионного реестра |
| V-10 | `*_latest.*` перезаписываются | engine.py, adaptive_dubbing_adapter.py | Некорректные dev-логи |
| V-18 | i18n отсутствует в большинстве шаблонов | templates/*.html | Нарушение TZ локализации |
| V-20 | Зависимости без точного пина | requirements.txt | Нереродуцируемые сборки |
| V-14 | Reader API разделён между двумя Blueprint | translate_api.py, reader_api.py | Нарушение принципа SRP |

### МОЖНО ПЕРЕНЕСТИ В ВЕРСИЮ 2.0

*Улучшения, не влияющие на стабильность текущего релиза*

| ID | Проблема | Файл | Влияние |
|----|---------|------|---------|
| V-05 | Семь систем укорочения текста | engines/* | Сложность поддержки |
| V-07 | Три таблицы `_PUNCT_PAUSE_MS` | timing_fit, ada, punctuation | Несогласованные паузы |
| V-11 | Append-only общие логи | auto_dub_api.py, timing_fit.py | Затруднённая диагностика |
| V-12 | Параллельные дубляжи в общем `output/` | tts.py, auto_dub_api.py | Архитектурный риск при concurrency |
| V-13 | `segments` мутируется in-place | dubbing_engine/engine.py | Скрытые зависимости |
| V-16 | Orphan template `pipeline_dev.html` | templates/ | Мёртвый код |
| V-17 | Два UI-стека без интеграции | templates/, src/ | Ресурсное дублирование |
| V-19 | Два naming-конвенции логирования | весь проект | Сложность фильтрации логов |

---

## ЧАСТЬ 7. ПЛАН ИСПРАВЛЕНИЯ

*Без кода. Только последовательность работ.*

### Фаза 1: Критические исправления (до релиза 1.0)

**1.1 AUTO_TASKS TTL-эвикция**
- Определить допустимое время жизни завершённой задачи (30-60 минут)
- Добавить механизм очистки: при создании новой задачи или фоновым потоком
- Проверить всех потребителей AUTO_TASKS (studio_api, dev endpoints)
- Регрессионный тест: studio не теряет данные в пределах TTL

**1.2 TTS cleanup в auto-dub пути**
- Перенести вызов `cleanup_old_files()` в `finally` блок `_run_pipeline_inner`
- Убедиться что studio получает файлы до очистки (проверить порядок)
- Регрессионный тест: studio mix работает после pipeline

**1.3 Единый предиктор длительности**
- Определить `engines/dubbing_engine/predictor.predict_ms()` как единственный канонический предиктор
- Обновить все вызовы `estimate_tts_duration_ms()` в SSO и soft_sync на `predict_ms()`
- Регрессионный тест: качество дубляжа не деградирует

**1.4 Фиксация зависимостей**
- Сгенерировать `requirements.lock` или `pip freeze` с точными версиями
- Синхронизировать `requirements_desktop.txt` и `pyproject.toml` для pywebview

---

### Фаза 2: Архитектурные улучшения (до релиза 1.0, можно после)

**2.1 Внедрение DubbingSession как единой точки истины**
- DubbingSession(task_id) хранит: session_dir, пути всех артефактов, segments, timing_map
- Все модули получают session как аргумент, а не читают из глобальных переменных
- Подключить ProjectSession к реальным путям файлов

**2.2 Завершение cleanup_session() в pipeline**
- Вызывать `cleanup_session(task_id)` в `finally` блоке `_run_pipeline` с задержкой (после studio phase)

**2.3 Выделение единого утилитарного модуля**
- Создать `engines/utils/lang_utils.py` с единой `normalize_lang()`
- Убрать 5 копий из engine-модулей
- Создать `engines/utils/punct_pauses.py` с единой `PUNCT_PAUSE_MS`

**2.4 Разделение auto_dub_api и studio_api**
- Выделить общие структуры данных в `engines/dub_session_state.py`
- Studio читает через явный интерфейс, а не через импорт приватных функций
- Устранить circular dependency

**2.5 Унификация систем укорочения текста**
- Определить DubbingEngine._stage_adapt() как единственную точку входа для адаптации
- Deprecate прямые вызовы SSO и semantic_adaptation из auto_dub_api
- Постепенная миграция за 2-3 версии

---

### Фаза 3: Полировка (версия 2.0)

**3.1 i18n полный охват**
- `index.html`, `settings.html`, `translate.html`, `voice.html`, `projects.html`, `reader.html` — перевести все строки в JSON-файлы
- Автотест: расширить `test_i18n_keys.py` на все шаблоны

**3.2 Унификация logging**
- Стандартизировать на `tubedub.{module_name}` для всех engine-модулей
- `api.{name}` для всех API blueprint-ов
- Обновить `app_logging.py` для hierarchical filtering

**3.3 Очистка мёртвого кода**
- Удалить `templates/pipeline_dev.html` (orphan)
- Определить судьбу React `src/` стека — либо интегрировать, либо удалить
- Документировать deprecation path для soft_sync (флаг выключен)

**3.4 Структурированные логи по сессиям**
- Все dev-артефакты писать в `output/dev/{task_id}/`
- `*_latest` alias — опциональный симлинк
- Убрать append-mode для общих логов

**3.5 Регрессионные тесты после каждой фазы**
- Фаза 1 → запустить полный `pytest tests/`
- Фаза 2 → e2e тест дубляжа + studio flow
- Фаза 3 → UI тест локализации

---

## СВОДНАЯ ТАБЛИЦА НАРУШЕНИЙ

| ID | Нарушение | Критичность | Уверенность | Фаза |
|----|-----------|------------|-------------|------|
| V-01 | AUTO_TASKS не эвикируется | 🔴 Критично | Высокая | 1 |
| V-02 | ProjectSession не применяется | 🔴 Критично (арх.) | Высокая | 2 |
| V-03 | Circular: auto_dub↔studio | 🟠 Высокое | Высокая | 2 |
| V-04 | 3 предиктора длительности | 🟠 Высокое | Высокая | 1 |
| V-05 | 7 систем укорочения | 🟠 Высокое | Высокая | 3 |
| V-06 | TTS cleanup не работает | 🔴 Критично | Высокая | 1 |
| V-07 | 3 таблицы PUNCT_PAUSE_MS | 🟡 Среднее | Высокая | 3 |
| V-08 | _normalize_lang() × 5 | 🟡 Среднее | Высокая | 2 |
| V-09 | cleanup_session() не вызывается | 🟡 Среднее | Высокая | 2 |
| V-10 | *_latest.* перезаписываются | 🟡 Среднее | Высокая | 2 |
| V-11 | Append-only shared логи | 🟢 Низкое | Высокая | 3 |
| V-12 | Параллельные дубляжи в output/ | 🟡 Среднее | Средняя | 3 |
| V-13 | segments мутируется in-place | 🟡 Среднее | Средняя | 3 |
| V-14 | Reader API split | 🟡 Среднее | Высокая | 2 |
| V-15 | pywebview версии конфликт | 🔴 Критично (сборка) | Высокая | 1 |
| V-16 | Orphan template | 🟢 Низкое | Высокая | 3 |
| V-17 | Два UI-стека | 🟢 Низкое | Высокая | 3 |
| V-18 | i18n не охватывает шаблоны | 🟡 Среднее | Высокая | 2 |
| V-19 | Два naming-конвенции логов | 🟢 Низкое | Высокая | 3 |
| V-20 | Зависимости без пина | 🟠 Высокое | Высокая | 1 |

---

## АРХИТЕКТУРНЫЙ ДИАГНОЗ

### Текущая модель: Hybrid — "Shared Global State + Per-Run UUID Namespacing"

**Сильные стороны:**
- UUID-скоупинг файлов эффективно предотвращает коллизии артефактов
- `DubbingEngine`, `DubEngine` — полностью instance-based, без class-level state
- `STATE_LOCK (RLock)` корректно защищает `AUTO_TASKS` от конкурентных мутаций
- `copy.deepcopy` на ключевых handoff-точках
- `timing_fit.py` — образцовая изоляция через OS tempdir + guaranteed rmtree
- 191 автотест, 0 пропущенных

**Слабые стороны:**
- Нет единого Session-объекта как источника истины
- Pipeline-оркестратор (`auto_dub_api.py`) стал слишком большим (~5000 строк)
- Три независимых подхода к одной проблеме (укорочение, предсказание) вместо одного
- Изоляция сессий объявлена, но не применяется к файловой системе

**Общая оценка для v1.0:** Готова к выпуску при исправлении Критических нарушений (V-01, V-06, V-15). Остальные — управляемый технический долг.

---

*Аудит завершён. Все выводы основаны на чтении кода. Ни одна строка не изменена.*
*До утверждения отчёта изменения запрещены.*
