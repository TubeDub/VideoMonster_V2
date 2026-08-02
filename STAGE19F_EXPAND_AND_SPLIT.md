# Stage 19f — Expand Must Execute + Aggressive Post-Restore Split

## Одна фраза
Underfill >350 ms → expand **зобов’язаний** змінити текст + re-TTS (`expand_executed=true`); після restore split іде, поки **кожна** дитина `fill_ratio ≤ 1.25` (не зупинятися на 2 дітях).

## Priority (§A)
1. Measure TTS vs slot  
2. `|Δ|≤350` → ok + light atempo  
3. **underfill >350** → `expand_to_fill` / force raw-prefer → re-TTS + `expand_executed=true`; інакше `dead_air_risk` (не `TextSlotFitExpand`)  
4. **overflow / predicted>slot×1.25 / fill>1.25** → `force_split_until_fit` (2–8 дітей, recursive depth≤3), розширений timing, re-TTS  
5. Після text-change → обов’язковий re-TTS  
6. `|Δ|>800` → `dead_air_risk` / `overflow_unresolved` / `stage19f_partial`

## Call sites
| File | Change |
|------|--------|
| `engines/text_slot_fit.py` | `force_split_until_fit`, max_children=8, fill gate 1.25, `expand_to_fill` force-raw |
| `engines/closed_loop_timing.py` | no false `TextSlotFitExpand`, nuclear raw expand, iterative post-restore split + re-split oversized children, `stage19f` meta |
| `engines/segment_timing_qa.py` | `stage19f` diagnostic block |

## Forbidden
- `expand_executed=false` + `algorithm_reason=TextSlotFitExpand`  
- Child/segment with `fill_ratio > 1.25` after restore/split (re-split until max depth)  
- Bare `TextThenAtemo` as sole path when `|Δ|>350`  
- Stop split at 2 children while predicted still ≫ slot  

## Tests
```bash
pytest tests/test_stage19f_expand_and_split.py \
       tests/test_stage19e_post_restore_split.py \
       tests/test_stage19d_anti_truncate.py \
       tests/test_stage19c_production_fit.py \
       tests/test_stage19b_closed_loop_text_fit.py \
       tests/test_stage19_slot_fill.py \
       tests/test_stage18_hard_fail.py \
       tests/test_stage15_meaning_retention.py -q
```
**Result: OK**

## Acceptance (George Jr. cold)
- underflow >350 → `expand_executed=true` + `rewrite_iterations≥1` **або** чесний `dead_air_risk`  
- long restore → `post_restore_split=true`, **немає** дитини з `fill_ratio>1.25`  
- end overlap <500 ms  
- voice `uk-UA-OstapNeural`; retention ≥ 0.85  
