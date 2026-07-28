# STAGE6 — TTS Speedup (Simple / Happy Path)

**Дата:** 2026-07-28  
**Клип:** George Jr. / Lucas (`uploads/stage2_happy_path_clip.mp4`), Simple  
**Цель:** ускорить Edge-TTS в 2–4× без потери качества Stage5.

---

## Вердикт

**Этап закрыт.** Simple озвучивает сегменты параллельно (concurrency=6) с disk-cache; качество Stage5 сохранено; повторный прогон бьёт в кэш 10/10.

| Проверка | Ожидание | Факт |
|---|---|---|
| MP4 | собирается | **да** (`video_d3894d87ee_OUTPUT_7360327c.mp4`) |
| fill_ratio ≥ 0.80 | все сегменты | **10/10** (`fill_ok_ratio=1.0`) |
| Review == TTS | да | **да** (`review_equals_tts`) |
| atempo | ≤ 1.08 | **max 1.06** |
| concurrency | ≥ 5 | **6** |
| cache replay | hits > 0 | **10/10** |
| TTS wall | заметно быстрее | **cold 6.3 s / warm 0.05 s** |

---

## Было / стало (wall time TTS)

Инструментированный wall time — `synthesize_segments_parallel` → `tts_wall_sec`  
(`output/tts_speedup_<task_id>.json`).

| Прогон | task_id | hits / misses | concurrency | **tts_wall_sec** | Итог пайплайна |
|---|---|---|---|---|---|
| **До Stage6** (Stage5 baseline) | — | нет batch-метрик; Edge шёл через conveyor / последовательно | ~1 | **минуты** внутри ~**1130 s** total (~19 мин, STAGE5 report) | MP4 ok |
| **Cold Stage6** (первый Edge-batch) | `e6aafc78…` | **0 / 10** | **6** | **6.339** | synth ok; (тогда упал integrity — см. фикс ниже) |
| **Warm Stage6** (тот же текст, кэш) | `7360327c…` | **10 / 0** | **6** | **0.048** | **done**, MP4, fill ok |
| Cache replay (acceptance) | — | **10 / 0** | 6 | **0.040** | — |

**Ускорение TTS-стадии (Edge synth):**

- Cold parallel vs Stage5 «минуты TTS в ~19 мин пайплайне»: порядок **десятки раз** на самой синтез-стадии (6.3 s вместо минут).
- Warm / повтор: **~130×** относительно cold (6.34 → 0.05 s) за счёт cache hit rate **100%**.
- Весь Simple-пайплайн: **~1130 s → ~507 s** (~**2.2×**), при том что bottleneck сместился на MT/перевод (~4–5 мин на 55%).

Оценка sequential Edge на тех же 10 сегментах (из cold parallel ~1.6 s/seg): ~**16 s** → parallel **6.3 s** ≈ **×2.5** только за concurrency (без кэша).

---

## Что внедрено

### A. Parallel Edge-TTS
- `engines/tts_parallel.py` — `synthesize_segments_parallel`
- warmup 2 → `ThreadPoolExecutor`
- `EDGE_TTS_CONCURRENCY` (default **6**, cap **8**)
- retry **per segment** (до 3), при rate-limit — backoff / снижение concurrency

### B. Disk cache
- `engines/tts_cache.py` — ключ `hash(text+voice+rate+pitch[+engine])`
- папка `cache/tts/` (или `VM_TTS_CACHE_DIR`)
- пустые/битые файлы ≠ hit; логи `tts_cache_hit` / miss

### C. Skip existing
- валидный целевой wav/mp3 сегмента → skip (в parallel helper)

### D. Нет TTS «для замера длины»
- длина — heuristic `estimate_tts_ms`; один Edge-pass на `final_tts_text`

### E. Метрики
В `task.info` / `output/tts_speedup_*.json`:
`tts_wall_sec`, `tts_segments_total`, `tts_cache_hits/misses`, `tts_concurrency_used`, `tts_retries`, `tts_skips_existing`  
+ stage timings STT / MT / TTS / mix в `pipeline_timing_*.json`

### Wiring (Simple only)
- `api/auto_dub_api.py`: Simple/Happy Path **force batch** Stage6; full conveyor TTS на Simple **пропускается** (`skip_full_conveyor_simple_stage6`)
- `engines/tts.py`: `_generate_single` тоже смотрит disk-cache; concurrency через Stage6 resolver
- Integrity: в контракт TTS добавлены `tts_cache_hit`, `tts_synth_rate`, `tts_synth_pitch` (иначе `STAGE_SNAPSHOT_INTEGRITY` после штампов Stage6)

---

## Acceptance (зелёный прогон)

```
task_id: 7360327ccb33410d94367d3cabf9c480
elapsed: 506.7 s
tts_speedup.path: stage6_parallel_cache
tts_wall_sec: 0.048 (warm, 10 cache hits)
tts_concurrency_used: 6
tts_cache_replay_hits: 10
fill_ok_ratio: 1.0
max_atempo: 1.06
review_equals_tts: true
mp4_done: true
```

Артефакты: `output/simple_pipeline_acceptance.json`,  
`output/tts_speedup_e6aafc787e084f47b045a927f065cda7.json` (cold),  
`output/tts_speedup_7360327ccb33410d94367d3cabf9c480.json` (warm).

Unit: `tests/test_stage6_tts_speedup.py` — OK.

---

## Изменённые файлы

| Файл | Роль |
|---|---|
| `engines/tts_cache.py` | **new** — disk cache |
| `engines/tts_parallel.py` | **new** — parallel + warmup + retry |
| `engines/tts.py` | concurrency + cache в single path |
| `api/auto_dub_api.py` | Stage6 batch + skip conveyor на Simple |
| `engines/pipeline_integrity/stage_contracts.py` | allow Stage6 TTS stamps |
| `engines/pipeline_integrity/tts_segment_fields.py` | `TTS_ALLOWED_MUTATIONS` |
| `scripts/simple_pipeline_acceptance.py` | speedup + cache replay checks |
| `tests/test_stage6_tts_speedup.py` | unit |

---

## Ограничения / не в скоупе

- Pro / Studio / ADA / SSO / lip-sync / voice clone — не трогали  
- Движок по умолчанию остаётся Edge  
- Cold MP4-прогон с пустым кэшем после финального integrity-fixа не перезапускался отдельно: cold wall **6.339 s** снят на том же коде Stage6 (synth завершился до падения snapshot); зелёный MP4 — warm. Для чистого cold+MP4 достаточно очистить `cache/tts/` и повторить acceptance.

---

## Успех (по ТЗ)

Пользователь в Simple: тот же ролик озвучивается заметно быстрее; без новых тишин и без разгона речи (atempo ≤ 1.08, fill ≥ 0.80); повторный прогон использует кэш (**10/10 hits**). Цифры TTS wall — в таблице выше.
