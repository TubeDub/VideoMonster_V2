# DUB_MODULE_FIX_REPORT

Модуль: **Дубляж** (`/dub`) — VideoMonster V2  
Дата: 2026-06-23

## Изменённые файлы

| Файл | Назначение |
|------|------------|
| `api/auto_dub_api.py` | Скорость slot_fit, overflow, без двойного soft_sync, studio session без блокировки UI |
| `api/dub_api.py` | Inline preview готового MP4 |
| `templates/dub.html` | Кнопки «Просмотр», «Открыть в Dub Studio», overlay плеера |
| `static/js/dub.js` | Отмена авто-редиректа в Studio, i18n, preview/studio кнопки |
| `static/i18n/ru.json` | Ключи `dub.preview`, `dub.open_studio`, … |
| `static/i18n/uk.json` | То же |
| `static/i18n/en.json` | То же |
| `static/css/style.css` | `.dub-preview-*` overlay |
| `tests/test_slot_fit_pipeline.py` | default `max_attempts=3` |

## Исправления

### 1. Скорость пайплайна (`auto_dub_api.py`)

- **PipelineTimer** — стадии `extract`, `whisper`, `translation`, `tts`, `slot_fit`, `timing`, `mux`, `export` логируются через существующий `PipelineTimer` (без изменений архитектуры).
- **Без лишней regen TTS в slot_fit**: если `tts_ms <= slot_ms`, только time-stretch (`apply_hard_anchor_soft_end`), без `fit_segment_with_retry`.
- **Кэш fitted**: ключ `slot_fit_key` (text+voice+slot+rate+pitch) — повторный прогон не regen при неизменном контенте.
- **overflow_pct**: расчёт от `fitted_ms` vs `slot_ms`, порог «уже влезает» — `slot_ms`, не `target_ms` (исправлен «весь красный»).
- **max slot_fit attempts = 3** (default).
- **Без двойного soft_sync**: `_build_gap_adjusted_track_no_double_soft_sync` — для сегментов с `fitted_file` передаёт `_skip_soft_sync=True` в `fit_segment_audio`.
- **Parallel TTS** — без изменений (уже включён для `len(tts_groups) > 1`).

### 2. Перевод

Логика перевода **не изменялась**.

### 3. TTS / style_cfg

`_style_params_from_info` используется до TTS и timing; voice FX применяется после синтеза — без изменений логики, только порядок slot_fit сохранён.

### 4. Timing / slot fit

- Auto compress loop **до** studio session (как было).
- Порядок: shorten → regen TTS → stretch → warn при overflow > 15%.
- `_adaptive_dub_resolve` в timing — без изменений.

### 5. UI после дубляжа — остаёмся на `/dub`

- Удалён авто-редирект при `studio_ready` в `dub.js`.
- При `done`: скачать MP4, **«Просмотр»** (inline video), **«Открыть в Dub Studio»** (ручной переход).
- `_publish_studio_session_keep_running`: studio session сохраняется, но `status` возвращается в `running` до финала экспорта.

### 6. i18n

Новые ключи `dub.*`: `preview`, `open_studio`, `save_to_folder`, `error_title`, `voice_preview`, `redub_loaded`, …

### 7. Ошибки

Существующий `vmFriendlyError` используется в catch-блоках; hardcoded строки на dub-странице переведены на `vmT`.

## Тесты

```bash
python -c "import app; print('OK')"
python -m pytest tests/test_slot_fit_pipeline.py tests/test_soft_sync.py tests/test_regeneration.py -q
```

**Результат:** 16 passed, `import app` OK.

## Ручной тест (2‑мин видео)

> Агент не запускал полный 2‑мин прогон — оценка ускорения ниже теоретическая.

1. Открыть `/dub`, загрузить MP4 ~2 мин (оригинал без `_OUTPUT_`).
2. Язык перевода RU, голос Edge, Whisper `tiny`, стиль «Современный».
3. Запустить **Дубляж**, дождаться 100% **на странице /dub** (не должно перекидывать в Studio).
4. Проверить: **Скачать MP4**, **Просмотр** (видео в overlay), **Открыть в Dub Studio** (ручной переход).
5. В dev-режиме: `output/pipeline_timing_*.txt` — сравнить доли `tts` / `slot_fit` / `timing`.
6. В Studio: overflow-индикаторы не должны быть все красными при нормальном fit.

## Ожидаемое ускорение (честная оценка)

| Оптимизация | Эффект |
|-------------|--------|
| Skip regen когда TTS уже в слот | −1…N вызовов TTS на «почти влезающих» сегментах |
| Кэш slot_fit_key | −повтор slot_fit при resume/export |
| Без двойного soft_sync | −~30–50% времени стадии `timing` на сегмент |
| max_attempts 3 vs 4 | −до 25% TTS в worst-case overflow |

**Для 2‑мин видео (~20 мин → ?):** ожидаемо **15–25% быстрее** при типичном overflow-профиле; точный замер только на вашем железе и видео. Whisper/TTS доминируют — без смены модели радикального ускорения не будет.

---

## Обязательный алгоритм подготовки аудио сегмента (2026-06-24)

### Изменённые файлы

| Файл | Назначение |
|------|------------|
| `api/auto_dub_api.py` | `_prepare_segment_audio_for_mux`, `_log_dub_slot_fit`, pre-mux gate в `_build_timed_dub_track` |
| `engines/timing_fit.py` | `trim_trailing_silence`, `prepare_dub_segment_audio`, `no_speech_trim` в `fit_segment_audio` |
| `engines/translation_naturalizer.py` | `shorten_for_slot` — обёртка для шага сжатия текста |
| `static/js/dub.js` | dev console: вывод `[DubSlotFit]` из `progress_detail.slot_fit_log` |
| `tests/test_slot_fit_pipeline.py` | тест cap atempo 1.15x, обновлённые моки pipeline |

### Алгоритм (только модуль Дубляж, перед сборкой MP4)

1. **Без обрезки речи** — только хвостовая тишина (`trim_trailing_silence`); внутренние паузы сжимаются без изменения темпа голоса.
2. **Иерархия overflow** (лог `[DubSlotFit] seg=N step=stretch|compress|regen|warn slot_ms= tts_ms= ratio=`):
   - **Step 1 stretch**: `atempo` до **1.15x** максимум (`DUB_MAX_ATEMPO`)
   - **Step 2 compress**: `shorten_for_slot` → naturalizer / optimizer
   - **Step 3 regen**: повторный TTS с укороченным текстом
   - **Step 4 warn**: `container_status=red`, `slot_overflow=True` — речь не обрезается
3. **Pre-mux**: `len(audio) <= slot_ms + 75ms` перед добавлением в timed track; при fail — warn, без trim speech.
4. **Mux path**: `fit_segment_audio(..., no_speech_trim=True, max_atempo=1.15)` — отключён `trim_overlap`.

### Тесты

```bash
python -m pytest tests/test_slot_fit_pipeline.py -q
```
