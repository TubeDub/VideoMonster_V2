# Stage 17 — Kill dead air + force TTS = Final (Simple / Happy Path)

## Symptom (George Jr. UK ~2:54)
- Dead air where EN had continuous speech.
- Speech ends before `slot_end`.
- Review Final clean; ears hear cut / wrong phonetic stream.

## Changes (files + constants)

| Area | File | Constants / behavior |
|------|------|----------------------|
| A underfill | `engines/text_slot_fit.py` | `UNDERFILL_EXPAND_RATIO=0.90`, `EXPAND_AIM_RATIO=0.95`, `UNDERFILL_ATEMPO_SLOW_RATIO=0.88`; `dead_air_risk_ms`; action `atempo_slow` |
| A/B audio | `engines/timing_fit.py` | `MAX_INTER_SEG_DEAD_AIR_MS=350`, `MAX_MICRO_PAUSE_MS=150`, `UNDERFILL_STRETCH_RATIO=0.90`; per-seg `atempo_slow`; `close_inter_segment_dead_air` (slow → micro-pad → **boundary_shift**) |
| Happy Path caps | `api/auto_dub_api.py`, `engines/happy_path.py` | atempo up **≤1.15**, slow **≥0.95**; `allow_atempo=True` on Happy Path |
| C voice/text | `engines/tts_lang_lock.py`, `engines/tts.py` | `DEFAULT_UK_CYRILLIC_MIN=0.55`; `uk-UA*` only; **fail loud** `PIPELINE_VOICE_LOCALE` / `PIPELINE_LANG_MIX` (Simple) |
| C Final | `engines/pipeline_integrity/tts_segment_fields.py` | `resolve_segment_text_for_tts` → Final + Stage 15 restore |
| D audit | `engines/dead_air.py` | silence ∩ EN mask → `dead_air_regions[]`; `append_dead_air_to_trace` |
| D Review | `engines/translation_review.py` | `dead_air_regions`, `dead_air_warning`, per-seg `slot_ms` / `tts_ms` / `dead_air_ms` / `voice_id` |
| Tests | `tests/test_stage17_dead_air.py` | underfill / gap / non-uk / retention 0.85 |

### Untouched (E)
`MIN_WORD_RETENTION=0.85`, glossary post-only, Marian / mt_cache / skip_cache_long, Stage 16 phrase map, `strip_slot_pad_fillers`.

## Log fields
Per segment (timing_fit + Review + `translation_trace.log` phase=`dead_air`):
- `slot_ms`, `tts_ms`, `dead_air_ms`, `voice_id`, `tts_text_hash` (== Final hash)
Post-mux: `task.info.dead_air_regions[]` `{start_ms,end_ms,duration_ms,en_speech}`

## George Jr. before/after (cold-run)

> Cold-прогон делает пользователь после этого патча. Ниже — **ожидаемые** зоны проблем до Stage 17 (из симптома ~2:54) и как заполнять после прогона.

| # | Approx EN zone (pre) | Issue | After Stage 17 (fill after cold-run) |
|---|----------------------|-------|--------------------------------------|
| 1 | ~0:40–0:55 | underfill / early stop | `dead_air_ms`↓; strategy contains `atempo_slow` / `gap_close` |
| 2 | ~1:20–1:40 | inter-seg hole >0.5s | gap ≤350 ms or EN-pause only |
| 3 | ~2:00–2:20 | speech vs Final mismatch | `tts_text_hash` == Final; `voice_id=uk-UA-*` |
| 4 | ~2:40–2:54 | tail dead air | `dead_air_regions` empty or EN pause |

**After cold-run:** paste from `task.info.dead_air_regions` and first lines of `translation_trace.log` phase=`dead_air`.

### voice_id
Expect: `uk-UA-OstapNeural` (or project Polina) — never `cs-CZ*` / `sk-SK*`.

### dead_air_regions
- **Before:** non-empty on EN-speech intervals (holes >350 ms).
- **After (acceptance):** `[]` or only intervals where EN timing also paused.

## Acceptance checklist
1. Ears: no >0.5 s holes on EN speech.
2. TTS text == Final (hash).
3. `voice_id` = `uk-UA*`.
4. #1/#5/#6/#9 meaning (Stage 15/16).
5. `dead_air_regions` OK.
6. pytest green.

## Tests
```bash
pytest tests/test_stage17_dead_air.py tests/test_stage15_meaning_retention.py -q
```
