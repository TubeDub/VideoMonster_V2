# STAGE12b — Fix truncated MT segments (Simple)

**Дата:** 2026-07-29  
**Цель:** #5/#6/#9 не отдавать из короткого cache+glossary — skip-cache-long + incomplete 0.55.

---

## Grep / wiring

| Проверка | Статус |
|---|---|
| `_SHORT_RATIO = 0.55` | ✅ |
| `long_src = words>30 OR oversized` | ✅ |
| `words_src>55 and words_tgt<40` | ✅ |
| `_skip_cache_long` в `mt_batch` | ✅ default ON |
| incomplete → unlink + None | ✅ |

---

## Изменения

### `engines/mt_cache.py`
- incomplete (a)(b)(c) по ТЗ 12b  
- entity: smash / ejected / survived / race cars / Star Wars / George Lucas  

### `engines/mt_batch.py`
- явный `_skip_cache_long(text)` перед `lookup_mt_cache`  
- opt-out: `VM_MT_SKIP_CACHE_LONG=0|false|no|off`

---

## Тесты

```text
pytest tests/test_stage12b_truncated_mt.py tests/test_stage12_lang_lock.py -q
```
