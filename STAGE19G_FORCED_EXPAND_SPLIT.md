# Stage 19g — Forced Real Expand + Aggressive Independent Split (fill ≤ 1.15)

## Одна фраза
Текст **зобов’язаний** змінювати довжину під слот (expand / split); кожна дитина після split отримує свій re-TTS і своє вимірювання; fill ≤ 1.15.

## Priority
1. Measure TTS vs slot  
2. underfill >350 ms **або** fill < 0.92 → `expand_to_fill` + fallback (raw → glossary → soft pads → noun repeat) → re-TTS  
3. overflow / predicted > slot×1.15 → `force_split_until_fit` (max 12, depth 4)  
4. light atempo only if `|Δ|≤350`  
5. else honest `dead_air_risk` / `overflow_unresolved` / `stage19g_partial`

## Call sites
| File | Change |
|------|--------|
| `engines/text_slot_fit.py` | `MAX_CHILD_FILL=1.15`, recursive `force_split_until_fit`, expand fallbacks |
| `engines/closed_loop_timing.py` | 2-attempt forced expand, independent child split (no parent duration), `stage19g` |
| `engines/segment_timing_qa.py` | `stage19g`, `text_changed`, `stage19g_split_depth` |

## Forbidden
- `expand_executed=true` without real text growth  
- Stop split while fill > 1.15  
- Copy parent `tts_ms` / `playback_duration` onto children  
- Sole atempo when `|Δ|>350`  
- Banned pad «ось як це було тоді»

## Tests
```bash
pytest tests/ -k "stage19g or expand or force_split" -q
pytest tests/test_stage19g_forced_expand_split.py tests/test_stage19f_expand_and_split.py … -q
```
**Result: OK**

## Acceptance (George Jr. cold)
- underfill >350 → `expand_executed=true` + `text_changed=true`  
- no child/segment with `fill_ratio > 1.15`  
- `split_children` ≥ 3–6 when needed  
- children have independent measured durations after re-TTS  
- end overlap <500 ms; voice `uk-UA-OstapNeural`  
