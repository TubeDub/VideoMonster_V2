# Stage 19i — Production-style Timing (CPS budget + bounded expand/split)

## Одна фраза
Підгонка як у кращих open-source дубляторах: CPS-бюджет → реальний expand/shorten → природний split (2.2–7.5 с) → atempo лише 0.90–1.20 → незалежний TTS кожної дитини.

## Priority
1. CPS-бюджет + контроль довжини тексту  
2. Unique natural split (речення / сильні коми)  
3. Independent child TTS + measure  
4. Bounded expand (semantic, не pad-спам)  
5. atempo тільки якщо `|Δ|≤350` і ratio ∈ [0.90, 1.20]  
6. Soft-pad ≤ 1 / сегмент, лише whitelist

## Constants
| Name | Value |
|------|-------|
| `TARGET_CPS_UK` | 14.0 |
| `MIN_CPS_UK` / `MAX_CPS_UK` | 11.5 / 17.0 |
| `MAX_CHILD_FILL` | 1.15 |
| `MAX_SPLIT_CHILDREN` / `DEPTH` | 10 / 3 |
| `MIN/MAX_CHILD_SLOT_MS` | 2200 / 7500 |
| `ATEMPO_MIN` / `MAX` | 0.90 / 1.20 |
| Soft pads | «саме тоді», «і саме в цей момент», «отже», «тому» |

## Call sites
| File | Change |
|------|--------|
| `engines/text_slot_fit.py` | `char_budget`, `estimated_cps`, CPS-aware split, expand strategy order, soft-pad ≤1 |
| `engines/closed_loop_timing.py` | stage19i meta, proportional speech slots, hard atempo band |
| `engines/segment_timing_qa.py` | `stage19i`, `char_budget`, `estimated_cps`, `soft_pad_count` |
| `tests/test_stage19i_cps_budget.py` | CPS / split / pad / atempo coverage |

## Forbidden
- `child.text == parent.text` / duration copy  
- soft_pad > 1 на сегмент  
- atempo < 0.90 або > 1.20  
- `final_status="ok"` при fill > 1.15 або dead_air > 350 мс  
- expand без росту `len(clean_text)`

## Tests
```bash
pytest tests/test_stage19i_cps_budget.py -q
pytest tests/test_stage19i_cps_budget.py tests/test_stage19h_unique_split.py … -q
```
**Result: OK**

## Acceptance (George Jr. cold)
- `avg_timing_score ≥ 95`  
- no `fill_ratio > 1.15`  
- underfill >350 → `expand_executed` + real growth  
- `unique_text_ok=true`, `soft_pad_count ≤ 1`  
- long monologues → ≥4–6 children  
- overlaps < 400 ms  
- `estimated_cps` ∈ 11.5–17  
- Voice: `uk-UA-OstapNeural`
