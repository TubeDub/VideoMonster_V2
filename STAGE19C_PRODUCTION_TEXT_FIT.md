# Stage 19c — Production text-fit

## Одна фраза
При `|TTS−slot|>350 ms`: expand/shorten/split тексту → обов’язковий re-TTS → atempo≤±15%; `regen_fn=None` при `text_changed` = fail; overflow>25% slot → split; заборона FitsNoChange/AudioStrategyNoTextRewrite як єдиної відповіді.

## Call sites
| Site | Change |
|------|--------|
| `apply_stage19b_rule_text_fit` | §B fail-loud `TextFitNoRegenError` / `PIPELINE_TEXT_FIT_NO_REGEN`; §D strong expand + optional LLM; §F `stage19c` meta + reason lock |
| `try_stage19c_overflow_split` | §C sentence split when `ov > max(3000, 0.25×slot)`; min child 800 ms |
| `run_closed_loop_timing` | Stage 19c split **before** adaptive resegment; Happy Path included |
| Resegment / split children | `max_iterations=0` = no LLM; **rule text-fit still runs** |
| `honest_diagnostics` / `overflow_strategy` | Anti-overwrite: locked `TextSlotFit*` not replaced by `AudioStrategyNoTextRewrite` |
| `text_slot_fit.expand_text_to_slot` | Up to 8 rule passes; `raw_mt_restore` when Raw longer |

Untouched: Stage 15 retention 0.85, Stage 16 pads/phrase map, Stage 18 hard-fail, Stage 19 public API names/ratios.

## Tests
```bash
pytest tests/test_stage19_slot_fill.py tests/test_stage19b_closed_loop_text_fit.py tests/test_stage19c_production_fit.py tests/test_stage18_hard_fail.py tests/test_stage15_meaning_retention.py -q
```
**Result: OK** (27 passed).

## George Jr. — before / after (expected)

| Seg | Before (`6d4869f4…`) | After (new task, cold) |
|-----|----------------------|-------------------------|
| #0 δ≈−737 | FitsNoChange / AudioOnly, expand_executed=false | TextSlotFit* / expand or atempo_slow fill≥0.90; `stage19c` filled |
| #8 δ≈+18s | pause_only, no shorten | TextSlotSplit (≥2 children) or shorten; δ≪18s |
| \|δ\|>350 | rewrite_iterations=0 | ≥1 if text/split; reason not AudioOnly sole |
| Outcome | green with holes | success без EN dead air **або** `PIPELINE_DEAD_AIR` |

Cold: `git pull` → `python desktop.py` (uk-UA-*, Final = TTS text).
