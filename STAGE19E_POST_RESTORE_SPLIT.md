# Stage 19e — Post-Restore Forced Split + Real Expand

## Одна фраза
Після anti-truncate / restore повний текст **зобов’язаний** бути розбитий на слоти; underfill >350 ms → `expand_executed=true` + re-TTS; гігантський overflow в один слот заборонений.

## Priority (§A)
1. Measure TTS vs slot  
2. `|Δ|≤350` → ok + light atempo (0.85–1.15)  
3. **underfill >350 ms** → forced `expand_to_fill` (prefer raw/semantic) → re-TTS → `expand_executed=true`  
4. **overflow >350 ms АБО після restore predicted > slot×1.25** →  
   - якщо predicted > max(slot×1.25, 3000) і > slot → **forced split** на 2–6 дітей з розширеним timing  
   - інакше `safe_shorten` (retention ≥ 0.85) → re-TTS  
5. Після split кожна дитина проходить свій closed-loop  
6. `|Δ|>800` після fit → `dead_air_risk` / `overflow_unresolved` / `stage19e_partial`

## Call sites
| File | Change |
|------|--------|
| `engines/text_slot_fit.py` | `should_force_split`, `split_into_slot_sized_chunks`, `prefer_raw` on `expand_to_fill`, fixed raw_prefer critical-marker gate |
| `engines/closed_loop_timing.py` | `try_stage19e_post_restore_split`, expanded child timing + neighbor shift, forced expand / dead_air, `stage19e` metadata, main-loop split+regen |
| `engines/segment_timing_qa.py` | `post_restore_split`, `split_children`, `stage19e` |

## Forbidden
- `expand_executed=false` silently accepted as ok when underflow >350 (→ `dead_air_risk`)  
- One segment with `fill_ratio > 1.5` after restore/anti-truncate (force split)  
- Bare `TextThenAtemo` / AudioOnly as sole path when `|Δ|>350`  
- Silent truncate (19d guard remains)

## Tests
```bash
pytest tests/test_stage19e_post_restore_split.py \
       tests/test_stage19d_anti_truncate.py \
       tests/test_stage19c_production_fit.py \
       tests/test_stage19b_closed_loop_text_fit.py \
       tests/test_stage19_slot_fill.py \
       tests/test_stage18_hard_fail.py \
       tests/test_stage15_meaning_retention.py -q
```
**Result: OK**

## Acceptance (George Jr. cold)
- underflow >350 → `expand_executed=true`, `rewrite_iterations≥1`, `fill_ratio≥0.90`  
- after anti-truncate / restore of long tail → `post_restore_split=true`, no segment with `fill_ratio>1.5`  
- end overlap <500 ms (ideally 0)  
- `truncation_blocked` works, retention ≥ 0.85  
- voice `uk-UA-OstapNeural`  
- JSON has no `expand_executed=false` with underflow >350 on ok segments  
