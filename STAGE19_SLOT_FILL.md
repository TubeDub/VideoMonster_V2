# Stage 19 — Підгонка речень під довжину слота (без дир, без втрати сенсу)

## Одна фраза
«Предложение подгоняется под длину исходника без потери смысла; слот заполнен ≥90%; нет быстро+дыра.»

## Constants (`engines/text_slot_fit.py`)
| Name | Value |
|------|-------|
| `UNDERFILL_EXPAND_RATIO` | 0.90 |
| `EXPAND_AIM_RATIO` | 0.95 |
| `UNDERFILL_ATEMPO_SLOW_RATIO` | 0.88 |
| `MAX_ATEMPO_SLOW` | 0.85 |
| `MAX_ATEMPO_FAST` | 1.15 |
| `FORBIDDEN_FAST_THEN_GAP` | True |

Priority on underfill: **expand text** → **atempo_slow** (0.85–1.0) → never leave EN-speech gap >350 ms.

## Changes
- `expand_to_fill` / richer `_rule_expand_once` (синоніми, повні форми; **no** Stage-16 pad fillers)
- `fit_text_to_slot`: `fill_ratio`, `atempo`, `strategy`, `predicted_tts_ms`; forbid `atempo>1.05 && fill<0.90`
- Review: `strategy`, `fill_ratio`, `atempo`, `tts_text_hash`, `predicted_tts_ms`
- Post-mux: `DeadAirError` **не** soft-swallow (`PIPELINE_DEAD_AIR` → `_fail`)

## Untouched
Stage 15 retention 0.85, Stage 16 phrase map / strip pads, Stage 18 hard-fail API, Marian/mt_cache.

## Tests
```bash
pytest tests/test_stage19_slot_fill.py tests/test_stage18_hard_fail.py tests/test_stage17_dead_air.py tests/test_stage15_meaning_retention.py -q
```

## Acceptance (George Jr. cold-run)
1. Немає «швидко сказав → тиша» на місці EN-мови.
2. `atempo` ∈ [0.85, 1.15]; fill ≥0.90 або `expand_then_slow` / fail.
3. Final/TTS = uk; TTS text == Final.
4. Success без `dead_air_regions` на EN **або** failed `PIPELINE_DEAD_AIR`.
