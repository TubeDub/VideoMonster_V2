# Stage 19b — Closed-loop text fit before tempo

## Одна фраза
При `|TTS−slot|>350 ms` спочатку expand/shorten тексту (Stage 19), re-TTS, потім atempo; `FitsNoChange` / `AudioStrategyNoTextRewrite` заборонені як єдина відповідь.

## Root cause
Happy Path передавав `max_iterations=0` → `run_closed_loop_segment` одразу `pause_only_after_resegment` без виклику `expand_to_fill` / `fit_text_to_slot`.  
Додатково: `FitsNoChange` штампувався при `need_adaptation=true` (underflow), а `AudioStrategyNoTextRewrite` лишався домінуючим `algorithm_reason`.

## Call sites → Stage 19
| Location | Change |
|----------|--------|
| `engines/closed_loop_timing.py` → `apply_stage19b_rule_text_fit` | New: `fit_text_to_slot` / expand·shorten → re-TTS → light atempo |
| `run_closed_loop_segment` | Stage 19b **before** LLM / before `max_iterations<=0` exit |
| `api/auto_dub_api.py` `_post_tts_max_retries` | Doc only: `0` = no LLM loops; rule fit still runs |
| `engines/pipeline_integrity/honest_diagnostics.py` | Recognize `TextSlotFit*` / `text_slot_fit` as real text adapt |
| `engines/dub_engine_v2/adaptation_decision.py` | `DURATION_DELTA_FORCE_ADAPT_MS` **500→350** |

Untouched Stage 19 API: `expand_to_fill`, `forbid_fast_then_gap`, ratios 0.90/0.95/0.88, atempo 0.85–1.15.

## FitsNoChange fix
- `TEXT_FIT_DELTA_MS = 350`, `UNDERFLOW_THRESHOLD_MS = 350`
- `FitsNoChange` only when `|delta|≤350` **and** `need_adaptation=False`
- If `|delta|>350` or `need=True` → `apply_stage19b_rule_text_fit`, never sole `FitsNoChange`

## Segment meta (after fit)
`expand_required`, `expand_executed`, `expansion_strategy`, `algorithm_reason` (`TextSlotFitExpand` / `TextSlotFitShorten` / `TextThenAtemo`), `fill_ratio`, `atempo`, `strategy`, `rule_rewrite_used`, `rewrite_iterations≥1` when text changed.

## Tests
```bash
pytest tests/test_stage19_slot_fill.py tests/test_stage19b_closed_loop_text_fit.py tests/test_stage18_hard_fail.py tests/test_stage15_meaning_retention.py -q
```
**Result: OK** (21 passed).

## George Jr. — expected before / after

| Seg | Before (task `6d4869f4…`) | After (expected cold run) |
|-----|---------------------------|---------------------------|
| #0 | underflow ~737, `FitsNoChange`, `expand_executed=false` | `expand_executed=true` **or** `atempo_slow` fill≥0.90; reason `TextSlotFit*` |
| #8 | overflow ~18 s, pause-only | shorten / text fit; overflow ≪ 18 s |
| all \|δ\|>350 | `AudioStrategyNoTextRewrite` / `pause_only_after_resegment`, `rewrite_iterations=0` | `TextSlotFit*` / `TextThenAtemo`, `rewrite_iterations>0` if text changed |
| QA | 9/9 `deferred_after_resegment` + pause_only | text+atempo attempt; else `dead_air_risk` / `PIPELINE_DEAD_AIR` |

Cold acceptance: `git pull`, `python desktop.py`, voice `uk-UA-*`, Final = TTS text.
