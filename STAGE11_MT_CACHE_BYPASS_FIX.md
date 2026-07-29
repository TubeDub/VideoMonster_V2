# STAGE11 — MT Cache Bypass Fix (Simple)

**Дата:** 2026-07-29  
**Скоуп:** Simple / Happy Path. Qwen / voice lock / pads — не трогали.

---

## Вердикт

**Закрыто.** Stage 10c (split / glossary / beams) больше не обходится старым cache: ключ **v3**, строже incomplete, glossary на cache hit, Review engine labels честные.

| Пункт | Статус |
|---|---|
| A. Key `v3_glossary_split` | ✅ |
| B. incomplete 0.50 + words>80/tgt<60 + critical entities | ✅ |
| C. glossary on cache hit (`finalize_mt_text`) | ✅ |
| D. `VM_MT_NO_CACHE=1` | ✅ |
| E. shorten refuse critical tail | ✅ |
| F. Engine `cache+glossary` / `marian*` | ✅ |
| G. pytest | ✅ |

---

## Корневая причина → фикс

1. **Cache hit → Marian skip** → v3 invalidates v2; glossary runs on hit anyway.  
2. **0.35 пропускал длинные обрезки** → ratio **0.50**, плюс `words_src>80 & tgt<60`, плюс Star Wars / Lucas / acceptance.  
3. **Файта из cache** → `finalize_mt_text` на hit + Review populate.  
4. **Shorten резал смысл** → refuse если пропали job / Star Wars / Lucas / робот / …

---

## Файлы

| Файл | Изменение |
|------|-----------|
| `engines/mt_cache.py` | v3 key, stricter incomplete, `VM_MT_NO_CACHE` |
| `engines/mt_batch.py` | glossary-on-hit, `mt_segment_engines` |
| `engines/mt/glossary_en_uk.py` | `finalize_mt_text` |
| `engines/translation_quality_log.py` | per-seg engines + finalize on synthesize |
| `api/auto_dub_api.py` | pass `mt_segment_engines`; job-cache finalize |
| `engines/text_slot_fit.py` | critical-tail shorten refuse |
| `tests/test_stage11_mt_cache_bypass.py` | **новый** |

---

## Env

- `VM_MT_NO_CACHE=1` — lookup всегда miss (отладка).  
- Логи / status: `mt_cache_hits`, `mt_cache_misses`, `mt_segment_engines`.

---

## Acceptance (ручной George Jr.)

1. v3 key достаточно (старый `cache/mt` v2 не бьёт) — или удалить `cache/mt`.  
2. Cold Simple en→uk: на miss Engine = `marian` / `marian_batch`; на hit = `cache+glossary`.  
3. Нет «Файта»; #9 Lucas/Зоряні; #3 идея «роботу» не убита shorten.

---

## Тесты

```text
pytest tests/test_stage11_mt_cache_bypass.py tests/test_stage10_mt_completeness.py tests/test_stage10b_mt_wiring.py tests/test_stage7_mt_speedup.py -q
→ 23 passed
```
