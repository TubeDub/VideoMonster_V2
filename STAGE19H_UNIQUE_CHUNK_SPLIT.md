# Stage 19h — Forced Unique-Chunk Split + Independent Child TTS

## Одна фраза
Після будь-якого split кожна дитина отримує **свій унікальний короткий кусок тексту** (не копію батька); split рекурсивний поки fill ≤ 1.15; кожна дитина — незалежний TTS + measure.

## Root cause (19g)
`deepcopy(parent)` лишав на дітях повний `raw_translation` / `semantic_engine_text`. Потім `expand_to_fill` / `force_raw_prefer` відновлював батьківський текст на кожну дитину → однаковий `final_tts_text` і гігантський fill.

## Priority
1. Unique text per child (`unique_text_ok`)  
2. Recursive `force_split_until_fit` while any child pred > slot×1.15 (max 12 / depth 4)  
3. Independent re-TTS + measure (no parent duration copy)  
4. Real expand only if underfill > 350 ms and text actually grows  
5. atempo only if `|Δ|≤350`

## Constants
| Name | Value |
|------|-------|
| `MAX_CHILD_FILL` | 1.15 |
| `MAX_SPLIT_CHILDREN` | 12 |
| `MAX_SPLIT_DEPTH` | 4 |
| `STAGE19H_OK_FILL_LO/HI` | 0.85 / 1.15 |
| `UNDERFLOW/OVERFLOW_TRIGGER_MS` | 350 |
| Soft pads | «саме тоді», «і саме в цей момент», «тоді», «отже» |

## Call sites
| File | Change |
|------|--------|
| `engines/text_slot_fit.py` | `assert_unique_split_chunks`, recursive unique `force_split_until_fit`, 19h soft-pad whitelist |
| `engines/closed_loop_timing.py` | `_scope_child_text_anchors`, refuse non-unique split, `stage19h` meta, `needs_re_tts` |
| `engines/segment_timing_qa.py` | `stage19h`, `unique_text_ok`, `stage19h_split_depth` |
| `tests/test_stage19h_unique_split.py` | unique chunks, recursion, no parent duration, metadata |

## Forbidden
- `child.text = parent.text` / same `final_tts_text` on multiple children  
- `child.tts_duration = parent.tts_duration` (any measured/tts duration copy)  
- Stop split while fill > 1.15 and depth < 4  
- `final_status="ok"` when fill > 1.15 or `|delta| > 350`  
- Soft pad «ось як це було тоді»

## Tests
```bash
pytest tests/test_stage19h_unique_split.py -q
pytest tests/test_stage19h_unique_split.py tests/test_stage19g_forced_expand_split.py tests/test_stage19f_expand_and_split.py tests/test_stage19e_post_restore_split.py -q
```
**Result: OK**

## Acceptance (George Jr. cold)
- No child with `final_tts_text == parent text` → `unique_text_ok=True`  
- No segment with `fill_ratio > 1.15`  
- `split_children ≥ 3` on long segments (Haskell / USC / Star Wars)  
- `stage19h_split_depth ≥ 2` when original overflow > 10 s  
- `avg_timing_score ≥ 90`, overlaps < 500 ms  
- `expand_executed=true` only when text grew  
- No `dead_air_risk` on underfill > 350 ms after expand  
- Tail (last 2 segments) fill ≤ 1.15  
- Voice: `uk-UA-OstapNeural`
