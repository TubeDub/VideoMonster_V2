# P16 — Production Hardening

**Дата:** 11 июля 2026  
**Статус:** Foundations реализованы (fast CI + opt-in long-run)

## Deliverables

| Подэтап | Модуль / команда |
|---------|------------------|
| P16.1 Long Run | `engines/production_hardening/long_run.py` — `--long-run-sec` (5s CI / 8h–24h lab) |
| P16.2 Resources | `resource_manager.py` — snapshot, temp WAV cleanup, leak thresholds |
| P16.3 Concurrency | `concurrency.py` — parallel projects/scheduler, UUID uniqueness |
| P16.4 Backcompat | `backcompat.py` — legacy OpenDDF/ZIP + contract migrate |
| P16.5 Fault Injection | `fault_injection.py` — missing/corrupt WAV, contract, UUID, scheduler, merge, OpenDDF |
| P16.6 Memory Budget | ResourceSnapshot (RSS/threads/open files/temp MB) |
| P16.7 Logging | `enriched_logging.py` + wiring in `fail_pipeline` |
| P16.8 Checklist | `checklist.py` + `scripts/run_p16_hardening.py` |
| P16.9 RC scenarios | synthetic long_film/short_clip/interview/podcast/cartoon/youtube/multilang |
| P16.10 Final Acceptance | checklist item `final_acceptance` |

## Commands

```bash
make harden              # fast P16 (~minutes)
make harden-long         # 30 min sample without nested pytest
python scripts/run_p16_hardening.py --long-run-sec 28800   # 8h
python scripts/run_p16_hardening.py --long-run-sec 86400   # 24h
```

## Tests

`tests/test_production_hardening_p16.py`

## Acceptance note

Полный P16.10 «готов к релизу» требует успешного lab-прогона 8h/24h на целевой машине.
CI закрывает структурные gates (faults, concurrency, checklist, logging, backcompat).
