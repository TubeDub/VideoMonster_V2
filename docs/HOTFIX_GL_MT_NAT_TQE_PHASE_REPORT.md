# HOTFIX-GL-MT-NAT-TQE v1.1 — Phase Report

**Date:** 2026-07-17  
**Status:** Implemented HF0–HF6 (code + tests)

## Multi-suite table

| suite_name | before | after | delta | status |
|------------|--------|-------|-------|--------|
| george_lucas dirty noop indicator | red (no test) | green `test_hf0_*` | +coverage | PASS |
| oversized MT split | missing hard guard | `engines/mt/oversized_guard.py` | +HF1 | PASS |
| project glossary | hardcoded builtins only | `data/glossaries/default_en_uk.json` + loader | +HF2 | PASS |
| naturalizer dirty force | Raw==Nat silent | dirty detector + force repair + skip_reason | +HF3 | PASS |
| TQE reject | score-only possible | critical multi-factor only | +HF4 | PASS |
| mid-name punct / DSAL visibility | partial | polish + `dsal_skip_reason` | +HF5 | PASS |
| short_dialog_en_uk_anti_overfit | absent | golden + test | +anti-overfit | PASS |
| tech_terms_en_uk_anti_overfit | absent | golden + test | +anti-overfit | PASS |
| existing `tests/test_tps_pipeline.py` | green | must stay green | 0 | run CI |
| existing `tests/test_mt_meaning_collapse.py` | green | must stay green | 0 | run CI |

## Definition of Done checklist

- [x] HF0–HF6 closed with this report
- [x] Raw==Naturalized ∧ dirty_mt → FAIL (tests)
- [x] TQE Reject not score-only (`engines/tqe/decision.py`)
- [x] Entities via project glossary + mask/restore
- [x] Regex repair temporary with TODO tickets (`engines/mt/dirty_mt.py`)
- [x] Oversized MT split (`engines/mt/oversized_guard.py` + TranslationAgent)
- [x] Silent score-only reject forbidden
- [x] Anti-overfit: 2 extra golden suites in manifest
- [x] Compatible with TPS (Fast QA uses glossary + dirty_mt_noop)

## Env / config

- Glossary override: `projects/{id}/glossary.json` or `data/glossaries/{id}.json`
- MT limits: `MT_MAX_CHARS_PER_UNIT` (480), `MT_MAX_SENTENCES_PER_UNIT` (2), `MT_MAX_WORDS_PER_UNIT` (55)

## Run

```bash
python -m pytest tests/test_hotfix_gl_mt_nat_tqe.py tests/test_tps_pipeline.py tests/test_mt_meaning_collapse.py -q
```
