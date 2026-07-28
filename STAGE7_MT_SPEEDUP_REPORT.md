# STAGE7 — MT / Translation Speedup (Simple / Happy Path)

**Дата:** 2026-07-28  
**Клип:** George Jr. / Lucas (`uploads/stage2_happy_path_clip.mp4`), Simple  
**Контекст:** Stage6 убрал TTS-узкое место; MT съедал ~4–5 мин (~55% пайплайна).

---

## Вердикт

**Этап закрыт.** Simple больше не гоняет AI-Core translation agent (deep-translator + десятки retries) и streaming_text; вместо этого — Marian batch 1:1 + disk-cache.

| Проверка | Ожидание | Факт |
|---|---|---|
| MP4 | да | **да** |
| bleed | 0 | **0** |
| fill_ratio ≥ 0.80 | да | **fill_ok_ratio=1.0** |
| Review == TTS | да | **да** |
| mt_wall_sec | ≪ 4–5 мин | **cold 28.4 s / warm 0.0 s** |
| cache replay | hits > 0 | **10/10** |
| parity 1:1 | да | **mt_parity_ok** |

---

## Было / стало

### Базовая диагностика Stage6 (до Stage7)

Из `pipeline_performance` / `ai_core_report` на том же клипе:

| Источник | Значение |
|---|---|
| `stage_times_sec.translate` | **~243–293 s** |
| AI-Core `translation` agent | **~138–163 s**, engine=`deep-translator`, **retries≈75** |
| `streaming_text` после TPS-skip | **~92–114 s** |
| `pipeline_timer.translation` | **0.0** (метрики не писались — agent path) |
| Total Simple (Stage6 warm) | **~507 s** |

### Stage7

| Прогон | mt_wall_sec | mt_calls | cache hits/misses | engine | total pipeline sec |
|---|---|---|---|---|---|
| **baseline Stage6** | ~**140–290** (translate stage) | per-seg + retries | n/a (job-cache miss bug) | deep-translator | **~507** |
| **Stage7 cold** | **28.413** | **1** | **0 / 10** | **marian_batch** | **335.4** |
| **Stage7 warm** | **0.0** | **0** | **10 / 0** (job_cache) | job_cache | **331.3** |
| MT cache replay | **0.02–0.10** | 0 | **10 / 0** | cache | — |

**Ускорение MT-стадии:** cold **~5–10×** vs Stage6 translate wall; warm — **секунды/нули** на перевод.  
**Пайплайн:** **~507 → ~335 s** (~**1.5×** overall); MT больше не половина времени.

---

## Что сделано

### A. Batch MT (Marian)
- `engines/mt_batch.py` → `translate_segments_batch(...)`
- батч до 10–15; cold: **1 Marian `generate()` на 10 сегментов**
- строго `len(out) == len(in)`

### B. Disk-cache
- `engines/mt_cache.py` — ключ `hash(text+src+tgt+engine)`, папка `cache/mt/`
- hit → не звать движок; miss → перевести → сохранить

### C. Параллель
- online fallback: concurrency 2–4 с retry на сегмент
- локальный Marian batch: 1 воркер на batch

### D. Убраны лишние проходы в Simple
1. **Skip AI-Core translation agent** (deep-translator + retries)
2. **Skip streaming_text orchestrator** (`tps_skip_orchestrator=True` на Happy Path / Simple policy)
3. LLM naturalizer / rewrite не обязателен на Stage7 path

### E. Метрики
`mt_wall_sec`, `mt_segments`, `mt_batch_size`, `mt_calls`, `mt_engine`, `mt_cache_hits/misses`  
→ `task.info` + `output/mt_speedup_<task_id>.json`

### Fix
- `save_translate_cache` ключ совпал с `load_translate_cache` (раньше warm job-cache никогда не попадал)

---

## Acceptance

**Cold** `8a510b5f…`: MP4 ok, mt_wall=28.4s, marian_batch, calls=1, fill_ok=1.0, Review==TTS, mt_cache_replay 10/10.  
**Warm** `2b400126…`: mt_wall=0.0 (job_cache), hits=10, MP4 ok.

Unit: `tests/test_stage7_mt_speedup.py` — OK.

---

## Изменённые файлы

| Файл | Роль |
|---|---|
| `engines/mt_cache.py` | **new** — disk MT cache |
| `engines/mt_batch.py` | **new** — batch + cache + parallel fallback |
| `api/auto_dub_api.py` | Simple Stage7 path; skip agent/streaming |
| `engines/simple_dub_pipeline.py` | `tps_skip_orchestrator` в policy |
| `engines/pipeline_cache.py` | fix job-cache key mismatch |
| `scripts/simple_pipeline_acceptance.py` | mt_speedup + cache replay checks |
| `tests/test_stage7_mt_speedup.py` | unit |

---

## Успех (по ТЗ)

MT не съедает половину пайплайна; cold **×2+** (факт **×5–10** на MT wall); warm-повтор — **0 s / секунды** на перевод с кэшем. Цифры `mt_wall_sec` — в таблице выше.
