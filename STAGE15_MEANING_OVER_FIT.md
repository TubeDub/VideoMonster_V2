# STAGE15 — Stop Final/TTS content truncation (Simple)

**Дата:** 2026-07-31  
**Симптом:** #1 Raw полный → Final обрезан на «не міг не відчувати»; #6 TTS короче Final.

## Приоритет

`meaning completeness > timing fit > atempo (≤1.15)`

## Фикс

### `engines/text_slot_fit.py`
- `MIN_WORD_RETENTION=0.85`, severe=`0.70`
- Refuse shorten if retention &lt; 0.85 / `_BAD_TAIL` / incomplete → `action=atempo_prefer`, `meaning_preserved=True`
- Critical markers: вижив, викину, розбив, потенціал, гоночн, фотоапарат, Лукас, Зоряні, Векслер, робота…
- `prefer_full_meaning_text()` — restore Raw MT when Final/TTS cut &gt;15% words

### Naturalizer / Review / TTS
- `accept_naturalizer_change`: rollback if words &lt; 0.85×Raw
- `auto_dub_api` post-fit: restore from `raw_translation`
- `resolve_segment_text_for_tts`: same restore even on locked `final_tts_text`
- `tts_review_align`: не сжимать Final к truncated spoken prefix

### Timing
- Happy Path `max_atempo` **1.15** (`happy_path.py`, `timing_fit.py`)

## Тесты

```text
pytest tests/test_stage15_meaning_retention.py tests/test_text_slot_fit.py -q
```
