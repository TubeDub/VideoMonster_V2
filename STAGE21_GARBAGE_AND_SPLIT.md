# Stage 21 — Kill Garbage Expand + Aggressive Forced Split

## Goal
1. Ban garbage expand (especially «Саме про … тут ідеться» and crumb appends)
2. On overflow > 350 ms or fill > 1.12 → force clean sentence/clause split (depth≤5, ≤14 children)
3. Independent TTS + measure for each child
4. Honest status — never mask overflow as `ok`

## Key changes
| Area | Change |
|------|--------|
| `GARBAGE_EXPAND_PATTERNS` / `is_garbage_expand` | Hard ban list + strip helper |
| `_stage19j_repeat_key_phrase` | Full entity sentence repeat — **removed** «Саме про …» |
| `expand_to_fill` | Refuse if garbage / soft_pad>1; else original |
| `force_split_until_fit` | `MAX_CHILD_FILL=1.12`, depth=5, children=14 |
| `try_stage19e_post_restore_split` | Trigger on overflow>350; stamp `stage21` |
| Status | `ok` only if clean + unique + 0.85≤fill≤1.12 |

## Forbidden in final_tts_text
- `Саме про … тут ідеться` (any variant)
- `, Джордж.` / `, Вісімнадцятирічний` crumbs
- `soft_pad_count > 1`
- Child that equals parent text

## Tests
`tests/test_stage21_garbage_and_split.py`
