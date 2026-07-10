# ТЗ: Word Timing Map (пословная синхронизация)

**Версия:** 1.3  
**Статус:** Phase 0 (обязательный сбор данных) — **текущий этап**  
**Согласовано:** Phase 0→4 roadmap; Legacy/Hybrid/Word Timing dev modes; A/B/C

---

## 1. Проблема

Сейчас оптимизация сегментов и подгонка TTS ориентируются на **длительность всего сегмента** и эвристическую оценку «сколько миллисекунд займёт текст». Это недостаточно:

- не видно, **где** внутри сегмента говорящий произносит каждую мысль;
- сокращения применяются ко **всему** тексту, а не к конкретным «переполненным» смысловым группам;
- TTS не знает, **в какой момент** должен прозвучать перевод конкретного слова оригинала;
- рассинхрон губ и речи остаётся высоким даже при 95–100% fill по длительности.

Whisper при STT уже возвращает (или может возвращать) **время каждого слова**. Эта информация теряется после первого шага.

---

## 2. Главная идея

Для каждого сегмента формируется **Word Timing Map** — упорядоченный список слов с абсолютными метками времени в исходном аудио:

```
George     start=0.00s  end=0.34s
was        start=0.35s  end=0.47s
driving    start=0.48s  end=0.91s
home       start=0.92s  end=1.36s
for        start=1.37s  end=1.57s
dinner     start=1.58s  end=2.10s
```

Карта:

1. **Сохраняется** от STT до финального микса.
2. После перевода проходит **семантическое выравнивание** (source unit ↔ target unit).
3. Используется **локальным оптимизатором** (сокращать только переполненные группы).
4. Передаётся в **TTS / timing_fit** для размещения смысловых блоков по времени оригинала.

---

## 3. Приоритеты (строго по убыванию)

| # | Приоритет | Описание |
|---|-----------|----------|
| 1 | Смысл | Не потерять смысл оригинала |
| 2 | Качество перевода | Не ухудшить качество перевода |
| 3 | Естественность | Максимально сохранить естественное звучание |
| 4 | Синхронизация | Максимально сохранить совпадение речи с оригиналом |
| 5 | Локальность | Изменять **только** проблемные части сегмента |
| 6 | SSO | SSO v2 — **только резерв**, не основной механизм |

**Цель:** не «уложить перевод в N секунд», а добиться **естественного совпадения** речи с оригиналом — слова перевода звучат в те же моменты, когда говорящий произносит соответствующие слова/мысли в исходном видео.

---

## 4. Модель данных

### 4.1 WordToken

```json
{
  "text": "George",
  "start_ms": 0,
  "end_ms": 340,
  "confidence": 0.94
}
```

- `start_ms` / `end_ms` — абсолютное время в исходном аудио (не относительно сегмента).
- `confidence` — опционально, из Whisper.

### 4.2 SegmentWordMap

Один сегмент пайплайна (после merge STT):

```json
{
  "segment_index": 0,
  "segment_start_ms": 0,
  "segment_end_ms": 2100,
  "words": [ /* WordToken[] */ ],
  "pauses_ms": [
    {"after_word_index": 2, "duration_ms": 120, "type": "natural"}
  ]
}
```

Паузы вычисляются как gap между `words[i].end_ms` и `words[i+1].start_ms` (если gap ≥ 80 ms).

### 4.3 SemanticUnit (после выравнивания)

```json
{
  "source_indices": [1, 2],
  "source_text": "was driving",
  "target_indices": [1],
  "target_text": "їхав",
  "start_ms": 350,
  "end_ms": 910,
  "budget_ms": 560,
  "est_ms": 480,
  "overflow_ms": 0,
  "mutable": false
}
```

- `source_indices` / `target_indices` — индексы слов в соответствующих картах.
- N:1 и 1:N соответствия **допустимы** (`at that moment` → `тоді`, `he was able to` → `зміг`).
- `mutable: false` — группа укладывается в бюджет; **текст менять запрещено**.

### 4.4 AlignedSegmentMap

Полная структура на сегмент после перевода + alignment:

```json
{
  "segment_index": 0,
  "source_words": [ /* WordToken[] */ ],
  "target_words": [ /* WordToken — est или post-TTS */ ],
  "units": [ /* SemanticUnit[] */ ],
  "total_budget_ms": 2100,
  "total_est_ms": 1980,
  "optimization_required": false
}
```

---

## 5. Пайплайн (согласованный порядок)

```
STT (Whisper + word timestamps)
  → SegmentWordMap[] (persist)
  → Translation (без изменений pipeline перевода)
  → Semantic Alignment (source ↔ target units)
  → Word Timing Optimizer (локально, только overflow units)
  → [Translation Review]
  → text_preparation / prosody
  → один TTS на сегмент (как сейчас)
  → timing_fit: placement + Time Stretch/Compression (2–8% max)
  → Pause redistribution (SSML / внутренние паузы по карте)
  → SSO v2 — ТОЛЬКО если WTM не справился (аварийный fallback)
  → DubEngine mix
```

**Word Timing Map — навигационная карта**, не движок по-словного синтеза.  
Edge TTS не умеет «сказать слово в 0.35 s»; карта используется для выравнивания смысловых блоков, решений об оптимизации и SSML-пауз, а не для отдельного TTS на каждое слово.

### 5.1 Гибрид WTM + SSO v2 (вариант B — согласовано)

| Роль | Модуль | Когда запускается |
|------|--------|-------------------|
| **Основной** | Word Timing Optimizer | Всегда при `VM_WORD_TIMING_MAP=1` |
| **Резервный** | SSO v2 | Только если после WTM сегмент **всё ещё значительно** превышает slot |

**Порядок оптимизации текста:**

1. Whisper строит Word Timing Map.
2. Перевод выполняется **без изменений** (Translation Pipeline не трогаем).
3. Semantic Alignment: сопоставление смысловых блоков source ↔ target.
4. Word Timing Optimizer:
   - перевод **полностью помещается** → **0 изменений** текста;
   - отдельные units не помещаются → **минимальная локальная** оптимизация **только** этих units;
   - units, которые уже укладываются → **`mutable: false`**, не трогать.
5. Если WTM решил проблему → **SSO не запускать**.
6. Если после WTM сегмент **всё ещё** существенно overflow → **SSO v2** как аварийный механизм (глобальное укорочение, как сейчас).

**Главные правила:**

- Сначала **всегда** Word Timing Map.
- Текст менять **только при реальной необходимости**.
- **Никогда** не переписывать весь сегмент, если проблема в одной части.
- SSO v2 **не удаляется** — остаётся резервом, не основой.

**Порог «значительного» overflow для SSO** (настраивается):

- `VM_WTM_SSO_FALLBACK_OVERFLOW_MS` — default `300` (если est − allowed > 300 ms после WTM → SSO).
- `VM_WTM_SSO_FALLBACK_RATIO` — default `1.08` (если est / allowed > 108% после WTM → SSO).

Оба условия: SSO только если **оба** порога превышены (избежать SSO при мелком хвосте).

При `VM_WORD_TIMING_MAP=0` — поведение как сейчас: только SSO v2 (или без оптимизации).

### 5.2 TTS и размещение (согласовано)

Word Timing Map **не** используется для синтеза каждого слова отдельно.

```
… → локальная оптимизация → один обычный TTS → Time Stretch/Compression (2–8% max) → финальная дорожка
```

| Шаг | Что делает WTM |
|-----|----------------|
| До TTS | Решает, **какой** текст и **где** паузы (SSML по original word gaps) |
| После TTS | `timing_fit` использует карту units как **якоря** для placement и допустимого stretch |
| Stretch | **2–8%** max (`atempo` / `ffmpeg`), не 15–30% как аварийный режим |
| Per-unit TTS | **Запрещено** в production (только dev-эксперимент) |

Существующая архитектура TubeDub (build_tts_groups → Edge TTS → timing_fit) **сохраняется**.

### 5.3 Что **не** меняется

- Translation Pipeline (router, MT, naturalizer)
- NER / preserved tokens
- Timing Engine **ядро** (segment boundaries из Whisper merge)
- **Один TTS на сегмент** (Edge TTS, build_tts_groups)

### 5.4 Что **дополняется**

| Модуль | Было | Станет |
|--------|------|--------|
| Оптимизация текста | SSO v2 — первый и единственный | **WTM Optimizer** — первый; SSO v2 — резерв |
| `timing_fit` | Segment slot + широкий atempo | + якоря units; stretch **2–8%** в норме |
| `professional_dubbing` prosody | Паузы по punctuation | + паузы по **original word gaps** из карты |
| STT | Только segment start/end | + **words[]** в timing_map |

---

## 6. Этапы реализации (согласованная дорожная карта)

### Phase 0 — Сбор данных (обязательная, **текущий этап**)

**Перед любыми изменениями логики дубляжа** Word Timing Map проходит весь пайплайн **без изменения поведения**.

| Разрешено | Запрещено |
|-----------|-----------|
| Сбор, сохранение, передача карты между модулями | Изменение текста перевода |
| Checkpoints после merge / translate / SSO / pre-TTS / final | Изменение таймингов |
| Dev-диагностика (`output/dev/word_timing_*.json/.log`) | Изменение синхронизации |
| Approximate Word Timing для SRT | Любое влияние на итоговый dub |

**Критерий успеха Phase 0:** итоговый дубляж **бит-в-бит** совпадает с текущим (Legacy) — любой символ или миллисекунда расхождения = **ошибка**.

**Checkpoints** (`engines/word_timing_map/phase0.py`):

```
post_merge → post_translate → post_sso → pre_tts → final
```

На каждом checkpoint проверяется: `segment_count`, `words_total`, `words_per_segment`, `real/estimated` — **не изменились** с момента `post_merge`.

### Phase 1 — Persist + диагностика

Word Timing Map проходит через весь пайплайн. **Никаких решений** — только передача данных.

- `source_word_maps` в task info
- `source_word_map` в каждом `segments_data[i]`
- `words[]` + `timing_source` в `timing_map_backup`
- Dev: `wtm_{task_id}.json`, `wtm_phase0_{task_id}.json`, `word_timing_{task_id}.log`

**Статус:** реализовано.

### Phase 2 — Heuristic Alignment Engine

```
Word Timing Map → AlignmentEngine (interface) → Meaning Units
```

MVP-эвристика: порядок слов, gaps, anchors (имена/числа/даты/валюты), punctuation, длина блоков.

Интерфейс `AlignmentEngine` — замена движка **без** переписывания Optimizer/TTS.

**Статус:** интерфейс готов; реализация — следующий этап.

### Phase 3 — Word Timing Optimizer (режим помощника)

Подключается только при `VM_WTM_OPTIMIZER=1` и `VM_WTM_SYNC_MODE=hybrid|word_timing`.

**Правила:**

1. Перевод помещается → **0 изменений**.
2. Overflow только в части сегмента → менять **только** эту Meaning Unit.
3. **Никогда** не переписывать весь сегмент.

`VM_WTM_AUTO_APPLY=0` (default) — Optimizer **только рекомендует** (assistant), текст не меняет.

### Phase 4 — Автоматическое применение

Только после большого числа успешных тестов: `VM_WTM_AUTO_APPLY=1`.

До Phase 4 Optimizer работает как **помощник**, не меняет текст автоматически.

### Legacy Pipeline — SSO v2

**Удалять SSO v2 запрещено.** Резервная система.

| Режим | Оптимизация текста |
|-------|-------------------|
| `legacy` (default, Phase 0–1) | Только SSO v2, как сейчас |
| `hybrid` (Phase 3+) | WTM Optimizer → SSO только при значительном overflow |
| `word_timing` (Phase 3+) | WTM Optimizer, без SSO |

Phase 0–1: **все три режима дают идентичный dub** (WTM только collect).

---

## 6.1 Режим разработчика — три режима сравнения

Переменная `VM_WTM_SYNC_MODE`:

| Режим | Phase 0–1 | Phase 3+ |
|-------|-----------|----------|
| `legacy` | WTM collect + SSO как сейчас | SSO primary |
| `hybrid` | WTM collect + SSO как сейчас | WTM → SSO fallback |
| `word_timing` | WTM collect + SSO как сейчас | WTM only |

Позволяет прогонять **одно видео** в трёх режимах и сравнивать регрессии.

---

## 6.2 A / B / C (согласовано)

| Тема | Решение |
|------|---------|
| **A. Alignment** | MVP — эвристика; модульный `AlignmentEngine` |
| **B. Гранулярность** | Merged 4–14 s; внутри — полная карта каждого слова; merge **не теряет** слова |
| **C. Preloaded SRT** | Approximate Word Timing (proportional); `timing_source=estimated`, `confidence=0.5` |

---

## 6.3 TTS и размещение (не меняется в Phase 0–1)

Word Timing Map — **карта выравнивания**, не по-словный синтез:

```
… → один TTS → stretch 2–8% → timing_fit → mix
```

Per-unit TTS **запрещён**.

---

## 6.4 (legacy section) Phase 5 — Pause Redistribution

Модуль: `engines/word_timing_map/redistribute.py` — Phase 3+ adjunct.

---

## 7. Интеграция с существующими модулями

### 7.1 `auto_dub_api.py`

```python
segments_data[i] = {
    "index": i,
    "text": "...",
    "plain_text": "...",
    "source_word_map": { ... SegmentWordMap ... },
    "aligned_map": { ... },  # после Phase 2
    ...
}
```

### 7.2 Translation Review

Новые поля в trace:

```
Word map:      6 words, 2 natural pauses
Alignment:     4 units (2× N:1)
Optimized:     unit#2 filler removed (−180ms)
Placement:     98% slot fill, 2 gaps redistributed
```

### 7.3 Dev reports

`output/dev/word_timing_map/wtm_{task_id}.json` — полная карта для отладки.

---

## 8. Переменные окружения

| Переменная | Default | Описание |
|------------|---------|----------|
| `VM_WTM_SYNC_MODE` | `legacy` | `legacy` / `hybrid` / `word_timing` (Phase 0–1: одинаковый dub) |
| `VM_WORD_TIMING_MAP` | `0` | Whisper real word timestamps (1=real, 0=approximate) |
| `VM_WTM_OPTIMIZER` | `0` | Phase 3: включить Word Timing Optimizer |
| `VM_WTM_AUTO_APPLY` | `0` | Phase 4: автоматически менять текст (0=assistant only) |
| `VM_WTM_FALLBACK_SSO` | `1` | Hybrid: SSO после неудачи WTM |
| `VM_WTM_SSO_FALLBACK_OVERFLOW_MS` | `300` | Порог ms для SSO fallback |
| `VM_WTM_SSO_FALLBACK_RATIO` | `1.08` | Порог ratio est/allowed для SSO |
| `VM_WTM_MAX_STRETCH_PCT` | `8` | Max stretch/compress после TTS (Phase 3+) |
| `VM_WTM_ALIGN_ENGINE` | `heuristic` | Alignment engine id |
| `VM_WTM_MIN_WORD_GAP_MS` | `80` | Порог «естественной паузы» |

---

## 9. Критерии приёмки

### Phase 0 (обязательно сейчас)

1. Word maps на **каждом** merged-сегменте (`real` или `estimated`).
2. Checkpoints `post_merge` … `final` — все `ok=true`.
3. **Нулевая регрессия:** dub-выход идентичен Legacy (символ и ms).
4. Dev-отчёты доступны в `output/dev/word_timing_map/` и `output/dev/word_timing_{task}.log`.

### Phase 2–4 (позже)

5. Meaning Units из Heuristic Alignment.
6. Optimizer меняет только overflow units; SSO не запускается если WTM справился.
7. Phase 4: auto-apply только после регрессионного набора тестов.

---

## 11. Пример (Meaning Units)

| Word | Start | End |
|------|-------|-----|
| George | 0.00 | 0.34 |
| was | 0.35 | 0.47 |
| driving | 0.48 | 0.91 |
| home | 0.92 | 1.36 |

**Translation:** `Джордж їхав додому.`

**Alignment:**

| Unit | Source | Target | Budget |
|------|--------|--------|--------|
| u0 | George | Джордж | 340ms |
| u1 | was driving | їхав | 560ms |
| u2 | home | додому | 440ms |

Если `їхав` est=620ms > budget 560ms → сократить **только u1** (например синоним или rate), u0 и u2 не трогать.

---

## 12. Риски и ограничения

- **Whisper word timestamps** неточны на fast speech / accents; нужен confidence filter.
- **Preloaded SRT** — Approximate Word Timing (`estimated`, confidence 0.5); WTM **не отключается**.
- **Per-unit TTS** — сознательно исключён (нестабильно, медленно).
- **Alignment quality** — главный риск Phase 2; heuristic для MVP.

---

## 13. Связанные документы

- Smart Segment Optimizer V2 (legacy duration-only)
- Professional Dubbing prosody
- `TRANSLATION_QUALITY.md`
