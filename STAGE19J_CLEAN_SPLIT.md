# Stage 19j — Clean Sentence Split + Safe Expand (no garbage)

## Одна фраза
Після split кожна дитина = закінчене речення або чисте підрядне; expand лише цілими природними фразами — ніколи обрізки на кшталт «, Джордж.» / «, Вісімнадцятирічний.».

## Root cause (19i)
`_stage19g_repeat_key_noun` робив `"{text}, {stem}."` → сміттєві хвости; word-mid split різав речення на крихти.

## Priority
1. Clean sentence/clause split (єдине джерело дітей)  
2. Заборона garbage-append в expand  
3. Independent TTS + measure  
4. CPS budget лишається  
5. atempo лише 0.90–1.20  

## Правила
- Split: спочатку `. ! ? ;`, потім коми + сполучники (`і`, `але`, `що`…)  
- Кожен chunk: `is_clean_utterance` + не prefix/suffix іншого  
- Expand: phrase repeat / glossary / soft_pad≤1 — лише якщо clean  
- Інакше `expand_executed=false`, чесний `dead_air_risk`  

## Метадані `stage19j`
`clean_split_ok`, `garbage_expand_blocked`, `char_budget`, `estimated_cps`, `soft_pad_count`, `unique_text_ok`, `final_status`

## Tests
```bash
pytest tests/test_stage19j_clean_split.py -q
```
**Result: OK**

## Acceptance (George Jr. cold)
- Немає «…, Джордж.» / «…, Вісімнадцятирічний.»  
- Усі діти — завершені речення/чисті підрядні  
- `unique_text_ok` + `clean_split_ok` = true  
- `avg_timing_score ≥ 94`  
- Зрозуміла українська в відео  
