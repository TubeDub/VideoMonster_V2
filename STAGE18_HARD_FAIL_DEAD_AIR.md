# Stage 18 — Hard-fail dead air + force uk TTS (Simple / Happy Path)

## Problem (after Stage 17 cold-run)
Job could **succeed** with dead air on EN-speech and/or non-uk phonetic stream (skip→silence, cache/voice bypass).

## Criterion «готово»
- **success** only if dub has no EN-speech dead air + uk Final voiced, **or**
- **failed** with `PIPELINE_DEAD_AIR` / `PIPELINE_VOICE_LOCALE` / `PIPELINE_LANG_MIX` — never quiet success with garbage.

## Files + constants

| Area | File | Change |
|------|------|--------|
| A hard-fail | `engines/dead_air.py` | `DeadAirError`, `enforce_dead_air_or_fail`, `PIPELINE_DEAD_AIR`; `VM_ALLOW_DEAD_AIR=1` = warning only |
| A call site | `api/auto_dub_api.py` (post timed export) | after mux audit → `enforce_dead_air_or_fail` → `_fail(..., error_code="PIPELINE_DEAD_AIR")`; trace `phase=dead_air_fail` |
| B no skip | `engines/tts_lang_lock.py` | Simple `fail_loud=True`: remt once → else **raise** (no `skip_tts` / empty) |
| C voice | `tts.py`, `tts_parallel.py`, `tts_lang_lock.py` | `assert_voice_matches_target(..., raise_error=True)`; ban `ru-RU`/`cs-CZ`/… for uk |
| D Final | `tts_segment_fields.py` | `resolve_segment_text_for_tts` → Final only when Final exists |
| E cache | `engines/tts_cache.py` | key `v2\|text\|voice\|lang\|…`; `VM_TTS_NO_CACHE=1` |
| F underfill | `timing_fit.py` | `dead_air_unresolved=True` if still >350 ms |
| G Review | already Stage 17 fields + `dead_air_warning` / fail code | |

### Untouched (H)
`MIN_WORD_RETENTION=0.85`, glossary post-only, Marian/mt_cache, Stage 16 phrase map, pad-filler ban.

### Stage 17 constants kept
`UNDERFILL_EXPAND_RATIO=0.90`, `EXPAND_AIM_RATIO=0.95`, `UNDERFILL_ATEMPO_SLOW_RATIO=0.88`, `MAX_INTER_SEG_DEAD_AIR_MS=350`.

## Hard-fail call site
```
_build_timed_dub_track → export timed mp3
  → audit_dead_air_post_mux(...)
  → enforce_dead_air_or_fail(regions)   # Simple/Happy Path
  → on DeadAirError: append_dead_air_to_trace(phase=dead_air_fail)
                     return _fail(..., error_code="PIPELINE_DEAD_AIR")
```

## Reproduce fail
1. **Voice:** force `cs-CZ-*` → `PIPELINE_VOICE_LOCALE` before/at Edge.
2. **Lang mix:** Final with low cyrillic + remt fail → `PIPELINE_LANG_MIX` (no empty segment).
3. **Dead air:** leave >350 ms silence inside EN timing → `PIPELINE_DEAD_AIR` (unless `VM_ALLOW_DEAD_AIR=1`).
4. **Cold TTS:** `VM_TTS_NO_CACHE=1` to ignore stale audio cache.

## Acceptance checklist (cold George Jr.)
1. Ears: no >0.5 s holes on EN speech; speech is Ukrainian.
2. `voice_id` = `uk-UA-*` per segment.
3. `tts_text_hash` == Final hash.
4. `dead_air_regions` empty or EN-pause only.
5. Artificial `cs-CZ` → job fail `PIPELINE_VOICE_LOCALE`.
6. Unclosed underfill → job fail `PIPELINE_DEAD_AIR`.

## Tests
```bash
pytest tests/test_stage17_dead_air.py tests/test_stage18_hard_fail.py tests/test_stage15_meaning_retention.py -q
```
