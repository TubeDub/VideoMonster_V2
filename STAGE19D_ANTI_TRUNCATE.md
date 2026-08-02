# Stage 19d — Anti-Truncate + Forced Text Fit

## Одна фраза
Текст **обов’язаний** заповнювати слот без втрати сенсу; atempo лише ±15% після re-TTS; тиха обрізка `final_tts_text` заборонена.

## Priority (§A)
1. Measure TTS vs slot  
2. `|Δ|≤350` → ok + light atempo  
3. underfill → **forced expand** (prefer raw/semantic) → re-TTS  
4. overflow → **shorten** or **split** (>25%/3s) → re-TTS  
5. then atempo 0.85–1.15  
6. `|Δ|>800` after fit → `dead_air_risk` / `overflow_unresolved` / `stage19d_partial`

## Call sites
| File | Change |
|------|--------|
| `engines/text_slot_fit.py` | `MAX_SILENT_TRUNCATE_RATIO=0.75`, `detect_silent_truncate`, `safe_shorten`, `semantic_anchor_text`, stronger `raw_prefer` |
| `engines/closed_loop_timing.py` | forced expand/shorten, `assert_no_silent_truncate` + `NeedReTTS`, `_stage19d_sanitize_algorithm_reason` (no bare `TextThenAtemo`), `_finalize_closed_loop_segment` |
| `engines/segment_timing_qa.py` | diagnostic fields: `shorten_executed`, `split_executed`, `truncation_blocked`, `retention_score`, `fill_ratio`, `stage19d` |

## Forbidden
- `TextThenAtemo` when `expand_executed=false` and underflow >350  
- Silent truncate (>25% words vs raw/semantic without `shorten_executed`)  
- `pause_only_after_resegment` / AudioOnly as sole path when `|Δ|>350`

## Tests
```bash
pytest tests/test_stage19d_anti_truncate.py tests/test_stage19c_production_fit.py tests/test_stage19b_closed_loop_text_fit.py tests/test_stage19_slot_fill.py tests/test_stage18_hard_fail.py tests/test_stage15_meaning_retention.py -q
```
**Result: OK**

## Acceptance (George Jr. cold)
- underflow >350 → `expand_executed=true`  
- overflow >350 → `shorten_executed` or `split_executed`  
- `truncation_blocked` restored or 0 silent cuts  
- retention ≥ 0.85 vs raw/semantic  
- voice `uk-UA-OstapNeural`; no long dead-air holes  
