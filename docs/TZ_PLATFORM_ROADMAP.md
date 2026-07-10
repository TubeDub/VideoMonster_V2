# ТЗ: Концепция дальнейшего развития TubeDub

**Версия:** 1.0  
**Статус:** Стратегическое ТЗ (roadmap платформы)  
**Продукт:** TubeDub V2 (*Powered by VideoMonster Engine*)  
**Дата:** 2026-06-17  
**Приоритет:** Долгосрочный (не блокирует текущий стабильный dub)

---

## Содержание

1. [Основание и цель](#1-основание-и-цель)
2. [Философия платформы](#2-философия-платформы)
3. [Архитектурные принципы](#3-архитектурные-принципы)
4. [Карта режимов (Studios)](#4-карта-режимов-studios)
5. [Общий медиа-стек (TubeDub Engine)](#5-общий-медиа-стек-tubedub-engine)
6. [Подсистемы](#6-подсистемы)
7. [AI Live Translation и прямые эфиры](#7-ai-live-translation-и-прямые-эфиры)
8. [Модуль записи и стриминга](#8-модуль-записи-и-стриминга)
9. [Студия звукозаписи](#9-студия-звукозаписи)
10. [Обучение дикторов и вокала](#10-обучение-дикторов-и-вокала)
11. [AI-помощник (сквозной)](#11-ai-помощник-сквозной)
12. [Модель проекта и данные](#12-модель-проекта-и-данные)
13. [Gap analysis (текущее состояние)](#13-gap-analysis-текущее-состояние)
14. [Roadmap по фазам](#14-roadmap-по-фазам)
15. [Ограничения и запреты](#15-ограничения-и-запреты)
16. [Связанные ТЗ](#16-связанные-тз)
17. [Критерии успеха платформы](#17-критерии-успеха-платформы)

---

## 1. Основание и цель

### 1.1 Проблема

Сегодня работа с видео, переводом, дубляжом, озвучкой, записью, стримингом и обучением голосу разбросана по десяткам отдельных программ. Пользователь вынужден:

- скачивать видео для перевода;
- ждать окончания batch-обработки;
- использовать отдельные DAW, OBS, караоке-приложения и speech-coach сервисы;
- терять контекст (тайминги, субтитры, перевод) при переходе между инструментами.

### 1.2 Цель

**TubeDub** должен развиваться не как программа только для видеодубляжа, а как **универсальная AI-платформа** для работы с:

- видео и аудио;
- голосом и музыкой;
- прямыми трансляциями;
- созданием профессионального медиаконтента.

**Главная цель:** объединить в одном приложении весь цикл — перевод, дубляж, запись, стриминг, субтитры, обработка голоса, вокал, монтаж — с **единым интерфейсом** и **постепенным расширением** без переписывания стабильных модулей.

### 1.3 Текущий фокус (не меняется)

На момент v1.0 roadmap **основное направление** остаётся:

> Профессиональный AI-дубляж готовых видеофайлов (batch pipeline).

Все новые подсистемы добавляются **поверх** существующего engine, не заменяя его.

---

## 2. Философия платформы

> Полная история и формулировка миссии: [TUBEDUB_HISTORY_AND_PHILOSOPHY.md](TUBEDUB_HISTORY_AND_PHILOSOPHY.md)

### 2.1 «Один engine — много студий»

Пользователь видит **режимы/студии** в UI. Под капотом — общие движки:

| Слой | Роль |
|------|------|
| **Studios (UI)** | Dub · Live Watch · Record · Broadcast · Voice Studio · Speech Coach · Vocal Coach · Translate |
| **Orchestration** | Пайплайны batch / live / record / stream |
| **Engines** | Ingest · STT · WTM · Translate · Naturalizer · TTS · Mix · Export · Analysis |
| **Infrastructure** | ModelManager · FFmpeg · Cache · Diagnostics · License |

### 2.2 UX-контракт

| Сценарий | Ожидание пользователя |
|----------|------------------------|
| Файл на диске | «Дубляж» → качественный результат (как сейчас) |
| Ссылка на видео | Вставил URL → выбрал язык → **«Смотреть»** без скачивания |
| Прямой эфир | Вставил ссылку на трансляцию → перевод **почти в реальном времени** |
| Запись | Экран / камера / микрофон → монтаж внутри TubeDub |
| Стрим | Запись + одновременный выход на YouTube / Twitch |
| Обучение | Микрофон → анализ → рекомендации → следующий фрагмент |

### 2.3 Приоритет качества vs скорости

| Режим | Приоритет |
|-------|-----------|
| Batch Dub | Качество перевода и синхронизации |
| Live Watch / Broadcast | Допустимая задержка 3–8 с, graceful degradation |
| Speech / Vocal Coach | Точность анализа, не latency |

---

## 3. Архитектурные принципы

### 3.1 Модульность (обязательно)

1. **Новый режим = новый orchestrator**, не правки в `auto_dub_api.py` без необходимости.
2. **Engines — переиспользуемые**, с единым контрактом (как `AlignmentEngine`, `TtsEngine`, `MtEngine`).
3. **Batch dub pipeline — эталон стабильности.** Регресс batch = блокер релиза.
4. **Feature flags / env** для каждой новой подсистемы (`VM_LIVE_*`, `VM_RECORD_*`, …).
5. **Phase 0 pattern:** сначала сбор данных и diagnostics без изменения поведения (как WTM Phase 0).

### 3.2 Плагинная структура каталогов (целевая)

```
engines/
  ingest/           # URL, HLS, file, camera, screen, network
  stt/              # batch + streaming STT (обёртка над stt_engine)
  word_timing_map/  # WTM — batch + live approximate
  translation/      # pipeline, router, naturalizer (существует)
  tts/              # batch + chunked streaming TTS
  mix/              # ducking, amix, loudness (dub_engine, professional_dubbing)
  live/             # ring buffer, latency budget, phrase queue
  record/           # screen, webcam, multi-mic capture
  broadcast/        # RTMP out, platform hooks (расширение engines/broadcast/)
  analysis/         # pitch, rhythm, diction, prosody metrics
  coach/            # speech + vocal training logic
  assistant/        # cross-studio AI recommendations
  model_manager/    # profiles: dub_quality | dub_fast | live | coach
api/
  auto_dub_api.py   # batch dub (stable)
  live_api.py       # future
  record_api.py     # future
  broadcast_api.py  # future
studios/            # UI routes + thin controllers per mode
```

### 3.3 Два профиля выполнения

| Профиль | STT | MT | TTS | Naturalizer | Sync |
|---------|-----|----|----|-------------|------|
| `quality` (batch) | Whisper full | deep + polish | Edge/batch | full V2 | WTM + timing_fit |
| `live` | faster-whisper chunks | fast + TM cache | chunked + crossfade | light / skip | adaptive buffer |

ModelManager должен уметь подготавливать компоненты **по профилю**, а не тянуть все модели сразу.

---

## 4. Карта режимов (Studios)

| Studio | Назначение | Статус |
|--------|------------|--------|
| **Dub** | Профессиональный дубляж файлов | ✅ Основной (production) |
| **Translate** | Текст, SRT, batch без полного mux | 🔶 Частично (pipeline есть) |
| **Live Watch** | URL → смотреть с AI-переводом и субтитрами | ⬜ Planned |
| **Broadcast** | Стрим out + live translation для зрителей | ⬜ Planned |
| **Record** | Экран, камера, микрофон, комбинации | ⬜ Planned |
| **Voice Studio** | Многодорожечная запись, обработка, dub edit | ⬜ Planned |
| **Speech Coach** | Обучение дикторов / актёров озвучки | ⬜ Planned |
| **Vocal Coach** | Караоке + анализ нот, ритма, дыхания | ⬜ Planned |
| **Assistant** | AI-рекомендации во всех студиях | 🔶 Зачатки (diagnostics, quality score) |

---

## 5. Общий медиа-стек (TubeDub Engine)

### 5.1 Batch pipeline (текущий, эталон)

```
Video/File
  → demux (FFmpeg)
  → STT (Whisper) + segments + WTM
  → translate + naturalizer + entity polish
  → SSO / WTM optimizer (hybrid)
  → TTS + prosody
  → timing_fit + mix
  → MP4 export
```

### 5.2 Live pipeline (целевой)

```
Stream URL / RTMP / capture
  → ingest (yt-dlp / FFmpeg / device)
  → ring buffer (audio + optional video passthrough)
  → VAD + streaming STT (partial transcripts)
  → phrase segmenter (rolling window)
  → fast MT (+ context window 2–3 фразы)
  → chunked TTS queue
  → ducking mix + playback delay buffer
  → live subtitles overlay (WebVTT / canvas)
  → optional record to file
```

### 5.3 Capture pipeline (целевой)

```
Screen / Webcam / Mic / Combo
  → multi-track capture
  → optional live ingest to Live/Broadcast pipeline
  → timeline editor (in-app)
  → export / stream out
```

---

## 6. Подсистемы

### 6.1 Универсальный перевод и дубляж (файлы + ссылки)

**Требования:**

- Автоматический перевод и озвучка видео **на любой поддерживаемый язык**.
- Работа с **готовыми файлами** (реализовано) и **интернет-ссылками** (planned).
- Источники: YouTube, Twitch, TikTok, Vimeo, вебинары, онлайн-курсы, подкасты, HLS/DASH, локальные и сетевые потоки.

**Ingest-слой (`engines/ingest/`):**

| Адаптер | Технология | Примечание |
|---------|------------|------------|
| Local file | FFmpeg | ✅ |
| HTTP/HLS/DASH | FFmpeg | planned |
| YouTube, Vimeo, TikTok, … | yt-dlp → stream URL | planned; ToS / DRM / geo |
| Twitch live | stream URL + reconnect | planned |
| Podcast/audio URL | audio-only demux | planned |
| Camera / screen | OS capture APIs | см. §8 |

**Режимы потребления:**

| Режим | Поведение |
|-------|-----------|
| Batch | Скачать/демux → полный dub → MP4 |
| Live Watch | Progressive decode → live pipeline → player |
| Subtitles only | STT + MT → overlay без TTS |

---

## 7. AI Live Translation и прямые эфиры

### 7.1 Пользовательский сценарий

1. Вставить ссылку на видеосервис или прямую трансляцию.
2. Выбрать язык перевода.
3. Нажать **«Смотреть»**.
4. TubeDub автоматически:
   - получает видео- и аудиопоток;
   - распознаёт речь **почти в реальном времени**;
   - переводит смысл;
   - синхронно озвучивает естественным голосом;
   - при возможности показывает переведённые субтитры;
   - сохраняет интонацию и эмоциональность (prosody-aware TTS + ducking).

### 7.2 Целевая задержка (latency budget)

| Этап | Budget |
|------|--------|
| Ingest + buffer | 1–2 с |
| STT partial | 1–2 с |
| MT | 0.3–0.8 с |
| TTS chunk | 1–2 с |
| Playback buffer | 1–2 с |
| **Итого (цель)** | **3–8 с** от произнесённой фразы |

### 7.3 Degradation (если не успевает)

1. Увеличить buffer (до N с, с предупреждением UI).
2. Только субтитры (TTS отключён).
3. Упрощённый MT (без naturalizer).
4. Reconnect ingest при обрыве сети.

### 7.4 Прямые эфиры стримера (Broadcast)

**Сценарий:** пользователь ведёт трансляцию; зрители слышат перевод на своём языке.

- Вход: микрофон / программный mix стримера.
- Live pipeline переводит речь ведущего.
- Выход: RTMP (YouTube, Twitch, …) с дорожкой перевода или replace audio.
- **Будущее:** AI-голос вместо оригинала с сохранением эмоций (voice conversion / expressive TTS).

### 7.5 Env vars (черновик)

| Variable | Default | Описание |
|----------|---------|----------|
| `VM_LIVE_ENABLED` | `0` | Master switch Live Watch |
| `VM_LIVE_LATENCY_TARGET_MS` | `5000` | Целевая задержка |
| `VM_LIVE_STT_MODEL` | `tiny` / profile | Модель streaming STT |
| `VM_LIVE_MT_FAST` | `1` | Fast MT lane |
| `VM_LIVE_TTS_CHUNK_MS` | `2000` | Размер TTS chunk |
| `VM_LIVE_SUBTITLES` | `1` | Live subtitles |
| `VM_BROADCAST_PIPELINE` | `0` | Уже есть — broadcast-grade MT |

---

## 8. Модуль записи и стриминга

### 8.1 Запись

Пользователь записывает:

- экран;
- веб-камеру;
- микрофон;
- любую комбинацию.

**Требования:**

- Многодорожечная запись (отдельные дорожки на timeline).
- Одновременная **прямая трансляция** на YouTube, Twitch и др.
- Во время эфира — **live translation** для зрителей (§7.4).
- Монтаж базового уровня без сторонних программ.

### 8.2 Технические компоненты

| Компонент | Описание |
|-----------|----------|
| `CaptureSession` | screen + webcam + N mics |
| `StreamPublisher` | RTMP / platform APIs |
| `TimelineProject` | дорожки, клипы, маркеры (§12) |
| `LiveBridge` | capture audio → live pipeline → stream mix |

### 8.3 Источники ingest (полный список)

- Интернет: URL, HLS, RTMP in
- Локально: файл, папка, watch folder
- Устройства: камера, экран, микрофон(ы)
- Сеть: RTSP, HTTP stream, NDI (перспектива)

---

## 9. Студия звукозаписи

### 9.1 Назначение

Профессиональная студия **внутри TubeDub** для озвучки, подкастов, аудиокниг, дубляжа с микрофона.

### 9.2 Функции

| Категория | Возможности |
|-----------|-------------|
| Запись | Несколько микрофонов одновременно, многодорожечность |
| Обработка | Noise reduction, EQ, compression, normalization |
| Dub workflow | Запись реплик → sync с видео → replace TTS |
| Монтаж | Cut, trim, fade, crossfade, базовый multitrack |
| Экспорт | WAV, MP3, MP4 mux, stem export |

### 9.3 Связь с Dub

- TTS-дорожка как reference; пользователь записывает поверх.
- WTM / segment timing как навигация по репликам.
- SSO не заменяет живую запись — только подсказка по длительности.

---

## 10. Обучение дикторов и вокала

### 10.1 Speech Coach (дикторы / актёры озвучки)

**Анализ:**

- произношение, дикция;
- темп речи;
- интонация, паузы, логические ударения;
- дыхание;
- эмоциональность, выразительность.

**Формат:** фрагмент текста → запись → метрики → рекомендации → следующий фрагмент.

**Переиспользование:** WTM, prosody engine, `language_intelligence`, quality score.

### 10.2 Vocal Coach (караоке)

**Сценарий:** пользователь поёт под минус; режим караоке по фрагментам.

**Анализ:**

- попадание в ноты (pitch tracking);
- ритм и темп;
- длительность нот;
- дыхание, дикция, произношение слов;
- ошибки исполнения.

**Педагогика:** после успешного прохождения сегмента → следующий (gamification optional).

### 10.3 Общий Analysis Engine

```
engines/analysis/
  pitch.py          # F0, cents offset
  rhythm.py         # onset, beat alignment
  diction.py        # phoneme / ASR compare
  prosody.py        # extend professional_dubbing/prosody
  breath.py         # pause / breath detection
  scoring.py        # unified score + feedback text
```

---

## 11. AI-помощник (сквозной)

### 11.1 Роль

Во **всех творческих разделах** — анализ и рекомендации по:

- переводу и calque;
- качеству дубляжа и синхронизации;
- озвучке и записи;
- монтажу и громкости;
- музыке и вокалу (в перспективе).

### 11.2 Реализация (эволюция)

| Этап | Что уже есть | Что добавить |
|------|--------------|--------------|
| v1 | `translation_review`, quality score, dev diagnostics | Unified Assistant panel |
| v2 | LLM rewrite (naturalizer V2) | Context-aware tips per studio |
| v3 | — | Proactive «fix this» actions with user confirm |

**Принцип:** assistant **рекомендует**, не меняет финальный контент без подтверждения (кроме явных auto-fix правил, как entity polish).

---

## 12. Модель проекта и данные

### 12.1 TubeDub Project (целевой формат)

Единый файл/папка проекта `.tubedub/`:

```json
{
  "version": 1,
  "studio": "dub | live | record | voice | coach",
  "media": {
    "source_type": "file | url | stream | capture",
    "source_uri": "...",
    "duration_ms": 0
  },
  "languages": { "source": "en", "target": "ru" },
  "timeline": { "tracks": [] },
  "translation": { "segments": [], "audits": [] },
  "word_timing_maps": [],
  "subtitles": [],
  "exports": []
}
```

### 12.2 Совместимость

- Текущий batch dub **не обязан** сразу писать full project file.
- Phase 1: optional `output/dev/project_{task_id}.json` (diagnostics).
- Phase 2: import/export project для Voice Studio и Record.

---

## 13. Gap analysis (текущее состояние)

| Возможность | Статус в коде |
|-------------|---------------|
| Batch dub файлов | ✅ `api/auto_dub_api.py`, `engines/dub_engine.py` |
| STT + Whisper | ✅ `engines/stt_engine.py` |
| Word Timing Map | 🔶 Phase 0 `engines/word_timing_map/` |
| Translation pipeline | ✅ `engines/translation_pipeline.py` |
| Professional dub / prosody | ✅ `engines/professional_dubbing/` |
| Model Manager | 🔶 `engines/model_manager/` (TZ отдельно) |
| URL ingest / yt-dlp | ⬜ |
| Live streaming STT | ⬜ |
| Live TTS queue | ⬜ |
| Player + live subtitles | ⬜ |
| Screen/webcam capture | ⬜ |
| RTMP stream out | ⬜ |
| Multi-mic DAW | ⬜ |
| Speech / Vocal coach | ⬜ |
| Unified project file | ⬜ |
| Broadcast pipeline | 🔶 `engines/broadcast/` (MT quality, не live) |
| Prepare SSE progress | ✅ `api/prepare_api.py` (pattern для live events) |

---

## 14. Roadmap по фазам

Фазы **независимы от WTM Phase 0–4** — параллельные треки. Нумерация **P** = Platform.

### P0 — Стабилизация ядра (текущий)

- Batch dub production-ready
- WTM Phase 0–1 (сбор данных, без регрессий)
- Model Manager по TZ
- Качество перевода (naturalizer, proper nouns, ru/uk)

### P1 — Universal Translate

- Studio «Translate»: текст, SRT, batch folder
- Общий ingest для **локальных** файлов (уже есть) + экспорт SRT/VTT

### P2 — Media Ingest (URL)

- `engines/ingest/` + yt-dlp adapter
- URL → demux → **batch dub** (скачивание в temp, не live)
- UI: поле «Ссылка» на странице Dub

### P3 — Live Watch (MVP)

- `engines/live/` ring buffer + streaming STT
- Fast MT lane + chunked TTS
- Player с задержкой 5–10 с, live subtitles
- YouTube/Vimeo VOD по URL

### P4 — Live Broadcast

- RTMP in/out
- Translation для зрителей стрима
- Reconnect, degradation modes

### P5 — Record Module

- Screen + webcam + mic capture
- Запись в timeline + export
- Stream out (OBS-like lite)

### P6 — Voice Studio

- Multi-mic, multitrack
- FX chain: NR, EQ, comp, normalize
- Dub-with-mic workflow поверх WTM

### P7 — Speech Coach

- Analysis engine MVP (diction, tempo, pauses)
- Fragment-based training UI

### P8 — Vocal Coach

- Karaoke + pitch/rhythm analysis
- Progressive segments

### P9 — AI Assistant

- Unified panel across studios
- Actionable recommendations

### P10 — Platform polish

- Single `.tubedub` project
- Cross-studio handoff (Record → Dub → Export)
- AI music editor (исследование)

---

## 15. Ограничения и запреты

1. **Не ломать batch dub** при добавлении live/record — отдельные orchestrators и feature flags.
2. **Не блокировать UI** загрузкой всех моделей — ModelManager profiles.
3. **Не обещать DRM-контент** — честное сообщение пользователю при недоступном потоке.
4. **Voice clone / replace streamer voice** — только с явным согласием и compliance (отдельное TZ).
5. **Новые студии** — сначала diagnostics / dev mode, потом UI для пользователя.
6. **Существующие TZ** (WTM, Model Manager) имеют приоритет над P2+ до закрытия Phase 0 критериев.

---

## 16. Связанные ТЗ

| Документ | Связь |
|----------|-------|
| `docs/TZ_AI_MEDIA_PLATFORM.md` | **Implementation TZ** — этапы 1–10, OSS, API, критерии приёмки |
| `docs/TZ_WORD_TIMING_MAP.md` | Sync batch + coach + live approximate timing |
| `docs/TZ_AI_MODEL_MANAGER_AND_DOWNLOAD_CENTER.md` | Profiles: quality / live / coach |
| `TRANSLATION_QUALITY.md` | Качество batch перевода |
| `STABLE_BASELINE.md` | Эталон стабильной версии dub |

---

## 17. Критерии успеха платформы

### 17.1 Batch (сохраняется)

- Dub regression test green на эталонных видео.
- Translation Review без критических артеfactов.

### 17.2 Live Watch (P3)

- YouTube VOD URL → воспроизведение с переводом без ручного скачивания пользователем.
- Median latency ≤ 8 с на референсном железе.
- Subtitles синхронны с озвучкой ±1 с.

### 17.3 Record + Broadcast (P4–P5)

- Запись экран+mic 10 мин без crash.
- RTMP out на test server стабилен 30+ мин.

### 17.4 Voice Studio (P6)

- 2+ mic simultaneous record → separate tracks → export stems.

### 17.5 Coach (P7–P8)

- Speech: ≥5 метрик на фрагмент + текстовая рекомендация.
- Vocal: pitch deviation в cents на экране в real time (≤200 ms UI update).

### 17.6 Platform (P10)

- Пользователь проходит сценарий **URL → Live Watch** и **File → Dub → Voice overdub → Export** без сторонних приложений.

---

*Документ фиксирует стратегическое направление. Детальные ТЗ на P2 (Ingest), P3 (Live Watch), P5 (Record) создаются отдельно при старте соответствующей фазы.*
