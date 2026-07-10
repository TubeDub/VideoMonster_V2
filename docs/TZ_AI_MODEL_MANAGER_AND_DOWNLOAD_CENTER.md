# ТЕХНИЧЕСКОЕ ЗАДАНИЕ (FINAL)
# VideoMonster V2 / TubeDub
# Подсистема: ModelManager + AI Download Center + Подготовка компонентов
# Формат: полная архитектурная спецификация
# Версия: 1.0
# Дата: 2026-06-19
# Приоритет: Критический

---

## Содержание

1. [Основание и цель](#1-основание-и-цель)
2. [Философия системы (UX-контракт)](#2-философия-системы-ux-контракт)
3. [Границы ответственности](#3-границы-ответственности)
4. [Архитектура ModelManager](#4-архитектура-modelmanager)
5. [Каталог компонентов (внутренний)](#5-каталог-компонентов-внутренний)
6. [Сценарий: первый запуск и подготовка](#6-сценарий-первый-запуск-и-подготовка)
7. [Сценарий: обычный dub EN→UK (пример)](#7-сценарий-обычный-dub-enuk-пример)
8. [Сценарий: повторный запуск (модели уже есть)](#8-сценарий-повторный-запуск-модели-уже-есть)
9. [Сценарий: нехватка места на диске](#9-сценарий-нехватка-места-на-диске)
10. [Сценарий: умная очистка (consent-based)](#10-сценарий-умная-очистка-consent-based)
11. [Сценарий: LRU при превышении лимита](#11-сценарий-lru-при-превышении-лимита)
12. [Сценарий: перенос хранилища на другой диск](#12-сценарий-перенос-хранилища-на-другой-диск)
13. [AI Download Center (UI владельца)](#13-ai-download-center-ui-владельца)
14. [API ModelManager](#14-api-modelmanager)
15. [Хранение данных и файловая структура](#15-хранение-данных-и-файловая-структура)
16. [Интеграция с существующими модулями](#16-интеграция-с-существующими-модулями)
17. [Ограничения и запреты](#17-ограничения-и-запреты)
18. [Диагностика](#18-диагностика)
19. [Тестирование](#19-тестирование)
20. [Критерии приёмки](#20-критерии-приёмки)
21. [План реализации (этапы)](#21-план-реализации-этапы)
22. [Текущее состояние проекта (gap analysis)](#22-текущее-состояние-проекта-gap-analysis)

---

## 1. Основание и цель

### 1.1 Проблема

VideoMonster использует множество офлайн-компонентов (Whisper, MarianMT, Argos, OCR, LLM и др.). Без централизованного управления:

- модели скачиваются в системный диск (`%USERPROFILE%\.cache\huggingface`);
- пользователь видит технические термины (HuggingFace, Marian, Cache);
- возможны повторные загрузки одной и той же модели;
- кэш растёт бесконтрольно;
- модули скачивают модели независимо друг от друга.

### 1.2 Цель

Создать **единую подсистему ModelManager**, которая:

1. Скрывает от пользователя всю «библиотечную» инфраструктуру.
2. Автоматически подготавливает компоненты перед dub.
3. Хранит модели в управляемой папке проекта (или на выбранном диске).
4. Не скачивает повторно целые модели.
5. Очищает кэш только с согласия пользователя (или по LRU при явном лимите).
6. Предоставляет владельцу **AI Download Center** для технического контроля.

### 1.3 Что НЕ меняется

**Translation Pipeline** остаётся без изменений логики:

```
Whisper → Router → MT → Naturalizer → Semantic → TTS → Timing → Mux
```

ModelManager — **инфраструктурный слой под pipeline**, не замена Router/MT.

---

## 2. Философия системы (UX-контракт)

### 2.1 Запрещённые понятия в пользовательском UI

Следующие слова **никогда** не показываются обычному пользователю (Simple/Pro mode):

| Запрещено | Причина |
|-----------|---------|
| HuggingFace | Технический бренд |
| Argos, Marian, NLLB, OPUS | Имена движков MT |
| Models, Model, Cache | «Библиотечная» терминология |
| Packages, Tokenizer, Hub | Dev-термины |
| pip install, download library | Ручная установка |

### 2.2 Разрешённые понятия для пользователя

| Показываем | Контекст |
|------------|----------|
| Язык оригинала | Выбор в dub |
| Язык перевода | Выбор в dub |
| «Видео переводится» | Основной процесс |
| «Подготовка компонентов» | Единственный экран загрузки |
| «Проверка компонентов…» | Быстрая pre-flight проверка |
| Названия компонентов (человеческие) | Whisper, Переводчик, Озвучка, OCR, Naturalizer |

### 2.3 Человеческие названия компонентов (mapping)

| Внутренний ID | UI (ru) | UI (en) |
|---------------|---------|---------|
| `whisper` | Распознавание речи | Speech recognition |
| `mt` | Переводчик | Translator |
| `tts` | Озвучка | Voice |
| `ocr` | Распознавание текста | Text recognition |
| `naturalizer` | Улучшение перевода | Translation polish |
| `semantic` | Смысловая адаптация | Semantic adaptation |
| `voice_fx` | Обработка голоса | Voice FX |
| `router` | Маршрут перевода | Translation routing |
| `llm` | Языковая модель | Language model |

**В Dev mode и AI Download Center** допускаются технические имена — только для владельца/разработчика.

---

## 3. Границы ответственности

### 3.1 ModelManager отвечает за

- Регистрацию всех AI-компонентов и их артефактов.
- Проверку наличия и целостности (`is_ready`, `verify_integrity`).
- Единую точку загрузки (`ensure_component`, `ensure_profile`).
- Запрет прямых `from_pretrained()` / `WhisperModel()` вне менеджера.
- Перенаправление HF env vars в project storage.
- LRU, лимиты, consent-based cleanup.
- Перенос storage root на другой диск.
- Статистику: размер, last_used, версия, путь.

### 3.2 ModelManager НЕ отвечает за

- Выбор маршрута перевода (Router).
- Quality Score, pivot fallback.
- Naturalizer prompts, Semantic rules.
- TTS голоса Edge (online API, не файловая модель).
- Pipeline cache JSON (`output/cache/pipeline/`).

### 3.3 AI Download Center

- UI-оболочка над ModelManager API.
- Доступ: **Owner host** или **Dev mode**.
- Не показывается в Simple mode.

---

## 4. Архитектура ModelManager

### 4.1 Модули

```
engines/
  model_manager/
    __init__.py          # публичный API
    registry.py          # каталог компонентов
    storage.py           # пути, env, disk selection
    downloader.py        # единая загрузка, dedup, progress
    integrity.py         # verify, corrupted cleanup
    lifecycle.py           # touch, last_used, LRU, consent cleanup
    profiles.py            # language-pair → required components
    config.py              # data/model_manager.json
```

**Текущий `engines/model_cache.py`** — прототип; при реализации **мигрирует** в `model_manager/` без изменения pipeline.

### 4.2 Диаграмма потоков

```
┌─────────────┐     ensure_profile(lang_pair)      ┌──────────────┐
│  Dub UI     │ ─────────────────────────────────► │ ModelManager │
│  (языки)    │ ◄──────── progress / ready ──────── │              │
└─────────────┘                                      └──────┬───────┘
                                                            │
                    ┌───────────────────────────────────────┼────────────────────────┐
                    ▼                   ▼                   ▼                        ▼
              Whisper STT          MT Engine           Naturalizer deps          OCR weights
                    │                   │                   │                        │
                    └───────────────────┴───────────────────┴────────────────────────┘
                                              │
                                    единое хранилище
                              {storage_root}/components/
```

### 4.3 Публичный API (Python)

```python
# engines/model_manager/__init__.py

def configure(app_dir: Path) -> None:
    """Startup: env vars, storage root, auto temp cleanup."""

def ensure_profile(
    app_dir: Path,
    source_lang: str,
    target_lang: str,
    *,
    progress_cb: Callable[[PrepareProgress], None] | None = None,
) -> PrepareResult:
    """
    Подготовить все компоненты для языковой пары.
    Один процесс, один progress stream.
    Не скачивает то, что уже есть и цело.
    """

def is_component_ready(app_dir: Path, component_id: str, *, variant: str = "") -> bool:
    """Быстрая проверка без сети."""

def list_components(app_dir: Path) -> list[ComponentInfo]:
    """Для AI Download Center."""

def delete_component(app_dir: Path, component_id: str, *, variant: str, force: bool = False) -> DeleteResult:
    """Только с force=True или после consent."""

def update_component(app_dir: Path, component_id: str, *, variant: str) -> UpdateResult:
    """Перекачать (verify → delete → download)."""

def get_storage_status(app_dir: Path) -> StorageStatus:
    """Размер, свободное место, лимит, last_cleanup."""

def set_storage_root(app_dir: Path, new_root: Path) -> MoveResult:
    """Перенос на диск D:/E:."""

def suggest_cleanup(app_dir: Path) -> list[CleanupCandidate]:
    """Кандидаты на удаление (давно не использовались)."""

def apply_cleanup(app_dir: Path, component_ids: list[str]) -> CleanupResult:
    """После согласия пользователя."""
```

### 4.4 Dataclasses

```python
@dataclass
class PrepareProgress:
    phase: str           # "check" | "download" | "verify"
    component_id: str    # внутренний
    label: str           # «Переводчик» — для UI
    percent: float       # 0..100 общий
    detail: str          # опционально, без технических имён

@dataclass
class ComponentInfo:
    id: str
    label: str
    variant: str         # e.g. "en-uk", "tiny", "base"
    size_bytes: int
    version: str
    last_used: str       # ISO8601
    path: str            # только в Download Center
    status: str          # ready | missing | corrupted | downloading
    engine_hint: str     # только owner view: marian, argos...
```

---

## 5. Каталог компонентов (внутренний)

### 5.1 Профиль языковой пары

Файл: `data/component_profiles.json`

```json
{
  "en->uk": {
    "components": [
      {"id": "whisper", "variant": "tiny"},
      {"id": "mt", "variant": "en-uk"},
      {"id": "naturalizer", "variant": "uk"},
      {"id": "tts", "variant": "uk"}
    ]
  }
}
```

**Router определяет variant MT** через `mt_pair_rankings.json` (marian en-uk, не nllb, если не ranked).

ModelManager **не дублирует** логику Router — запрашивает у Router/registry:

```python
needed = models_needed_for_pair(app_dir, src, tgt)  # уже есть
```

### 5.2 Mapping MT variant → artifact

| variant | artifact (internal) | ~size |
|---------|---------------------|-------|
| en-uk | Helsinki-NLP/opus-mt-en-uk | ~300 MB |
| en-ru | argos package en→ru | ~50 MB |
| en-de | Helsinki-NLP/opus-mt-en-de | ~300 MB |

Пользователь видит только: **Переводчик — подготовка…**

---

## 6. Сценарий: первый запуск и подготовка

### 6.1 Триггер

Пользователь на `/dub` выбрал:
- Источник: **English**
- Перевод: **Украинский**
- Нажал «Начать»

### 6.2 Pre-flight (≤2 сек если всё ready)

```
POST /api/prepare/start
{ "source_lang": "en", "target_lang": "uk" }
```

Backend:
1. `ModelManager.ensure_profile(en, uk)` — фаза `check`.
2. Если все `is_component_ready` → `{ "ready": true, "prepare": false }`.
3. UI сразу запускает dub.

### 6.3 Prepare UI (если чего-то нет)

**Overlay fullscreen**, блокирует dub до готовности.

```
┌────────────────────────────────────────────┐
│  Подготовка компонентов                     │
│  ████████████░░░░░░░░  62%                  │
│                                             │
│  ✔  Распознавание речи                      │
│  ⬇  Переводчик                              │
│  ✔  Озвучка                                 │
│  ✔  Распознавание текста                    │
│  ·  Улучшение перевода                      │
│                                             │
│  Осталось ~2 мин                            │
└────────────────────────────────────────────┘
```

**Запрещено** на этом экране:
- кнопки «Скачать», «Выберите модель»;
- имена Marian/HuggingFace;
- несколько progress bar для разных загрузок.

### 6.4 WebSocket / SSE progress

```
GET /api/prepare/stream?job_id=...
```

Events:
```json
{"type":"progress","percent":45,"label":"Переводчик","phase":"download"}
{"type":"done","ready":true}
{"type":"error","message":"Недостаточно места на диске","code":"disk_full"}
```

### 6.5 После завершения

Overlay закрывается → dub продолжается **без** повторной проверки в рамках сессии (TTL 24h или до смены lang pair).

---

## 7. Сценарий: обычный dub EN→UK (пример)

| Шаг | Действие | ModelManager |
|-----|----------|--------------|
| 1 | User: EN → UK | — |
| 2 | prepare/check | whisper tiny, mt en-uk, tts uk |
| 3 | mt missing | download 1 stream, label «Переводчик» |
| 4 | verify sha/size | integrity.check |
| 5 | touch last_used | lifecycle.touch |
| 6 | Pipeline start | Whisper → Router → MT (lazy load uses local path) |

**Один процесс загрузки**, sequential или parallel с общим percent (weighted by size).

---

## 8. Сценарий: повторный запуск (модели уже есть)

1. `is_component_ready("mt", "en-uk")` → `True` (local + integrity OK).
2. **Никакой сети**, prepare завершается за <500 ms.
3. `touch` обновляет last_used.

### 8.1 Запрет повторной загрузки

Перед download:
```python
if integrity.verify(local_path) == OK:
    touch(); return CACHED
```

Download только если: `missing | corrupted | version_mismatch (explicit update)`.

---

## 9. Сценарий: нехватка места на диске

### 9.1 Pre-check

Перед download:
```python
required = sum(artifact.size for artifact in pending)
free = shutil.disk_usage(storage_root).free
if free < required + SAFETY_MARGIN_MB:
    raise DiskSpaceError(required, free)
```

### 9.2 UI

```
┌────────────────────────────────────────────┐
│  ⚠ Недостаточно места на диске             │
│                                             │
│  Нужно: 1.2 ГБ                              │
│  Свободно: 340 МБ                           │
│                                             │
│  [ Освободить место ]  [ Выбрать другой диск ]│
│  [ Отмена ]                                 │
└────────────────────────────────────────────┘
```

**Не начинать** partial download без места.

---

## 10. Сценарий: умная очистка (consent-based)

### 10.1 Принцип

**Никогда** не удалять модели автоматически после каждого ролика.

Автоудаление **только**:
- temp/incomplete файлы (без consent);
- LRU **только** при превышении лимита **и** после предупреждения (см. §11).

### 10.2 Proactive suggestion

Раз в N дней (или при открытии Download Center):

```
┌────────────────────────────────────────────┐
│  Немецкий переводчик                        │
│  Не использовался 148 дней · 780 МБ         │
│                                             │
│  Удалить?   [ Да ]  [ Нет ]  [ Напомнить позже ]│
└────────────────────────────────────────────┘
```

API:
```
GET  /api/models/cleanup/suggestions
POST /api/models/cleanup/apply  { "component_ids": ["mt:en-de"], "confirmed": true }
```

---

## 11. Сценарий: LRU при превышении лимита

### 11.1 Настройки

`data/model_manager.json`:
```json
{
  "max_storage_gb": 10,
  "lru_enabled": true,
  "require_confirm_before_lru": true
}
```

### 11.2 Поведение

При `total_size > max_storage_gb`:

1. **Если `require_confirm_before_lru: true`** → modal «Кэш превысил 10 ГБ. Освободить место?» со списком кандидатов LRU.
2. **Если false** (owner opt-in) → удалить oldest `last_used` до достижения 90% лимита.

**Текущая реализация** (`enforce_size_limit` без confirm) — **не соответствует ТЗ**, требует доработки.

---

## 12. Сценарий: перенос хранилища на другой диск

### 12.1 First-run wizard (опционально)

```
Где хранить компоненты для перевода?
( ) Диск C:  (свободно 12 ГБ)
(•) Диск D:  (свободно 450 ГБ)
( ) Диск E:  (свободно 890 ГБ)
```

### 12.2 Реализация

```python
set_storage_root(app_dir, Path("D:/VideoMonster/models"))
```

1. Остановить активные downloads.
2. `shutil.move(old_root, new_root)` или copy+verify+delete.
3. Update `data/model_manager.json` → `"storage_root": "D:/..."`.
4. Reconfigure HF env vars.

---

## 13. AI Download Center (UI владельца)

### 13.1 Расположение

- Route: `/owner/download-center` или секция в Settings **только при** `owner_host === true`.
- **Убрать** текущую карточку «Кэш моделей» из обычных Settings (нарушает UX-контракт).

### 13.2 Таблица компонентов

| Колонка | Пример |
|---------|--------|
| Компонент | Переводчик (EN→UK) |
| Движок (owner) | Marian / opus-mt-en-uk |
| Размер | 312 МБ |
| Версия | 1.0 |
| Последнее использование | 2026-06-19 |
| Статус | ✔ Готов |
| Действия | Удалить · Обновить · Открыть папку |

### 13.3 Сводка

- Общий размер моделей
- Свободное место на storage disk
- Размер pipeline cache (отдельно)
- Дата последней очистки
- Топ-5 самых больших

---

## 14. API ModelManager

### 14.1 Public (dub flow)

| Method | Path | Описание |
|--------|------|----------|
| POST | `/api/prepare/check` | Быстрая проверка ready |
| POST | `/api/prepare/start` | Запуск подготовки, returns job_id |
| GET | `/api/prepare/stream/{job_id}` | SSE progress |
| GET | `/api/prepare/status/{job_id}` | Polling fallback |

### 14.2 Owner / Download Center

| Method | Path | Описание |
|--------|------|----------|
| GET | `/api/owner/components` | list_components |
| GET | `/api/owner/storage/status` | get_storage_status |
| POST | `/api/owner/components/delete` | delete (requires confirm) |
| POST | `/api/owner/components/update` | redownload |
| POST | `/api/owner/storage/limit` | set max GB |
| POST | `/api/owner/storage/root` | set_storage_root |
| GET | `/api/owner/cleanup/suggestions` | suggest_cleanup |
| POST | `/api/owner/cleanup/apply` | apply_cleanup |
| POST | `/api/owner/storage/open-folder` | open in Explorer |

### 14.3 Deprecate

Текущие `/api/models/cache/*` → migrate to `/api/owner/components/*` без HF-terminology в responses для public routes.

---

## 15. Хранение данных и файловая структура

```
{storage_root}/                    # default: {APP_DIR}/models
  components/
    whisper/
      tiny/                          # faster-whisper CTranslate2
    mt/
      en-uk/                         # HF hub snapshot (internal)
      en-ru/
    ocr/
    llm/
  hub/                               # HF_HUB_CACHE (symlink or subdir)
  transformers/
  tmp/                               # incomplete downloads

{APP_DIR}/cache/
  huggingface/tmp/                   # temp redirect
  pipeline/                          # JSON dub cache (unchanged)

{APP_DIR}/data/
  model_manager.json                 # settings, storage_root, limits
  model_cache_registry.json          # last_used, sizes (migrate)
  component_profiles.json
```

### 15.1 Env vars (set at startup, before any HF import)

```
HF_HOME              = {storage_root}
HUGGINGFACE_HUB_CACHE = {storage_root}/hub
TRANSFORMERS_CACHE   = {storage_root}/transformers
HF_HUB_CACHE         = {storage_root}/hub
```

---

## 16. Интеграция с существующими модулями

### 16.1 Обязательный рефакторинг загрузки

| Модуль | Сейчас | Должно быть |
|--------|--------|-------------|
| `marian_engine.py` | `from_pretrained()` direct | `ModelManager.get_mt_session(en, uk)` |
| `nllb_engine.py` | direct | через ModelManager |
| `stt_engine.py` | `WhisperModel(download_root=...)` | `ModelManager.get_whisper(size)` |
| `argos_engine.py` | argos package install | `ModelManager.ensure_argos_pair()` |
| `ocr_engine.py` | если есть HF/torch weights | через ModelManager |

### 16.2 Pipeline hooks

`auto_dub_api.py` — **перед** whisper/transcribe:

```python
prepare = model_manager.ensure_profile(app_dir, src, tgt, progress_cb=...)
if not prepare.ready:
    return error / wait
```

**Не менять** логику сегментов, naturalizer, mux.

### 16.3 Router

Router **не скачивает** — только сообщает ModelManager какой MT variant нужен через `models_needed_for_pair()`.

---

## 17. Ограничения и запреты

1. ❌ Любой модуль не имеет права вызывать `from_pretrained`, `WhisperModel`, `argos.install` вне ModelManager.
2. ❌ Автоудаление моделей после каждого dub.
3. ❌ Технические термины в Simple/Pro UI.
4. ❌ Несколько независимых progress dialog для загрузок.
5. ❌ Скачивание без проверки disk space.
6. ❌ Повторное скачивание целой модели.
7. ❌ Хранение HF cache в system profile без явного opt-in owner.

---

## 18. Диагностика

### 18.1 User-facing (Settings → Диагностика, без tech terms)

- ✔ Распознавание речи — готово
- ✔ Переводчик — готово
- ⚠ Недостаточно места — 340 МБ свободно

### 18.2 Owner Download Center

- Размер моделей: 4.2 ГБ / 10 ГБ лимит
- Свободно на D: 412 ГБ
- Pipeline cache: 120 МБ
- Последняя очистка: 2026-06-15
- Самые большие: whisper/base 780 МБ, mt/en-de 312 МБ

---

## 19. Тестирование

### 19.1 Автотесты (`scripts/test_model_manager.py`)

| Test | Проверка |
|------|----------|
| `test_configure_storage` | env vars → project path |
| `test_no_redownload_if_intact` | ensure twice → network mock called once |
| `test_integrity_corrupted` | truncated file → re-download |
| `test_prepare_profile_en_uk` | all components ready |
| `test_disk_full_blocks_download` | raises before write |
| `test_lru_requires_confirm` | no delete without confirm flag |
| `test_consent_cleanup` | suggest → apply → deleted |
| `test_move_storage_root` | files accessible after move |
| `test_public_api_no_tech_names` | prepare response labels |

### 19.2 E2E

1. Clean install → EN→UK dub → prepare overlay → success.
2. Second dub → no overlay (<1s).
3. Delete mt/en-uk in Download Center → next dub re-prepares only translator.

---

## 20. Критерии приёмки

| # | Критерий | Проверка |
|---|----------|----------|
| 1 | Пользователь выбирает только языки | UI audit |
| 2 | Все модели скачиваются автоматически | E2E first run |
| 3 | Один процесс подготовки | Single overlay + SSE |
| 4 | Нет повторных скачиваний целых моделей | Network mock test |
| 5 | Предупреждение при нехватке места | disk_full test |
| 6 | AI Download Center для owner | /owner/download-center |
| 7 | ModelManager — единая точка загрузки | grep: no direct from_pretrained |
| 8 | Перенос на другой диск | set_storage_root test |
| 9 | Consent-based cleanup | UI + test |
| 10 | LRU только с подтверждением (default) | test |
| 11 | Pipeline/Router/Whisper/TTS/Timing/Mux без регрессии | regression suite |
| 12 | Нет HuggingFace/Marian/Cache в user UI | UI audit |
| 13 | Temp/incomplete cleanup автоматически | startup test |

---

## 21. План реализации (этапы)

### Этап 1 — ModelManager core (2–3 дня)
- [ ] `engines/model_manager/` package
- [ ] Migrate `model_cache.py` → storage + lifecycle
- [ ] integrity, no-redownload
- [ ] Startup configure in app.py/desktop.py

### Этап 2 — Prepare flow (2 дня)
- [ ] `/api/prepare/*` + SSE
- [ ] Dub overlay UI («Подготовка компонентов»)
- [ ] Hook in auto_dub_api pre-flight

### Этап 3 — Engine refactor (2 дня)
- [ ] marian/nllb/stt/argos → ModelManager only
- [ ] Remove direct downloads

### Этап 4 — AI Download Center (1–2 дня)
- [ ] Owner page
- [ ] Remove «Кэш моделей» from public settings
- [ ] Migrate APIs

### Этап 5 — Consent + disk wizard (1–2 дня)
- [ ] Cleanup suggestions UI
- [ ] LRU confirm modal
- [ ] Storage root picker (first run)

### Этап 6 — Tests + regression (1 день)
- [ ] test_model_manager.py
- [ ] E2E EN→UK first/second run
- [ ] Full pipeline regression

**Итого: ~9–12 рабочих дней**

---

## 22. Текущее состояние проекта (gap analysis)

| Требование ТЗ | Статус | Комментарий |
|---------------|--------|-------------|
| HF cache в проекте | ✅ Частично | `engines/model_cache.py`, env vars |
| ModelManager как отдельный модуль | ⚠️ | Есть prototype `model_cache`, не полный MM |
| Единая точка загрузки | ❌ | marian/stt ещё direct from_pretrained |
| UX без «библиотек» | ❌ | Settings показывает «Кэш моделей», HF paths |
| Prepare overlay | ❌ | Нет |
| AI Download Center (owner) | ❌ | Нет отдельной страницы |
| Consent cleanup | ❌ | LRU auto без confirm |
| Disk picker | ❌ | Нет |
| No redownload | ⚠️ | model_is_local частично |
| Pipeline unchanged | ✅ | Pipeline не тронут |
| Tests | ⚠️ | `test_model_cache.py` — базовые, не full MM |

---

## Приложение A — Текст для overlay (ru)

```
Подготовка компонентов
Мы настраиваем всё необходимое для перевода вашего видео.
Это займёт несколько минут при первом запуске для выбранного языка.

[progress bar]

✔ Распознавание речи
⬇ Переводчик
· Озвучка
· Улучшение перевода
```

## Приложение B — Связь с Translation Router

ModelManager **не заменяет** Router v4 / MT registry. Sequence:

1. User selects langs.
2. ModelManager.prepare(profile from langs + router rankings).
3. Pipeline runs with existing Router → MT cascade.

---

*Документ подготовлен для передачи разработчику без дополнительных устных пояснений.*

---

## 23. Дополнение: Lazy Loading (оптимизация загрузки моделей)

### 23.1 Проблема

После внедрения ModelManager программа начала автоматически скачивать модели при старте (~3 ГБ → 16+ ГБ). Это противоречит архитектуре TubeDub.

### 23.2 Принцип

| Правило | Реализация |
|---------|------------|
| **Lazy Loading** | Модель скачивается только при первом использовании функции / языковой пары |
| **Нет автозагрузки при старте** | `set_downloads_permitted(False)` в `app.py` / `desktop.py` |
| **Единый кэш** | `engines/model_manager/` — один storage для dub / translate / STT / TTS / Reader |
| **Без повторного скачивания** | Проверка локального кэша + версии перед download |
| **Скрытая загрузка** | UI: «Идёт подготовка компонентов…» / «Идёт загрузка языкового пакета…» |
| **Подтверждение пользователя** | Диалог с размером (~XX МБ) перед первой загрузкой пары |
| **Менеджер моделей** | Настройки → «Менеджер моделей» + `/api/models/*` |
| **Очистка неиспользуемых** | LRU 90 дней (`cleanup_unused_days` в `data/model_manager.json`) |
| **Dev mode** | Пути, источник, журнал — только при `VM_DEV_MODE` / `VM_ARCHITECT_MODE` |

### 23.3 Feature-scoped profiles

| `feature` | Компоненты профиля |
|-----------|-------------------|
| `translate` | MT + naturalizer |
| `stt` | Whisper |
| `dub` | Whisper + MT + naturalizer + TTS |
| `tts` | TTS |

### 23.4 API

- `POST /api/prepare/check|start` — `feature` param
- `GET /api/models/components` — список для Settings
- `POST /api/models/delete|update|cleanup-unused|cleanup-all|download`

### 23.5 Frontend

- `static/js/prepare.js` — общий flow confirm → progress
- `translate.js`, `dub.js` — вызов `runLanguagePackPrepare({ feature })`
- `templates/settings.html` — карточка «Менеджер моделей»

### 23.6 Gate в runtime

```python
# engines/model_manager/runtime.py
_DOWNLOADS_PERMITTED = False  # default

with prepare_download_session():  # only during user-confirmed prepare
    ensure_profile(...)
```

`ModelNotPreparedError` — если модуль пытается загрузить без кэша вне prepare-сессии.

### 23.7 Тесты

- `scripts/test_lazy_downloads.py` — gate + profile scopes + Argos index-only

### 23.8 Статус внедрения (2026-06)

| Пункт | Статус |
|-------|--------|
| Download gate at startup | ✅ |
| Feature profiles | ✅ |
| Prepare API + shared JS | ✅ |
| Translate / Dub lazy prepare | ✅ |
| Settings → Менеджер моделей | ✅ |
| Dev-only technical details | ✅ |
| Owner Download Center migration | ⚠️ частично |
| Full startup audit (all engines) | ⚠️ ongoing |

