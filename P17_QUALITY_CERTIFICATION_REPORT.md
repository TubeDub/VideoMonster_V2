# P17 — Quality Certification & Release Governance

**Дата:** 11 июля 2026  
**Статус:** Foundations реализованы

## Цель

Перед каждым релизом подтвердить, что качество дубляжа не ухудшилось относительно Golden Release.

## Deliverables

| Подэтап | Модуль |
|---------|--------|
| P17.1 Golden Release | `golden_release.py` → `releases/golden_latest/` |
| P17.2 Quality Gates | `quality_gates.py` — overlap/overflow/sync/budget/determinism/RUNTIME_INTEGRITY |
| P17.3 UAT | `uat.py` — film/series/interview/podcast/documentary/youtube/short |
| P17.4 Config Freeze | `config_freeze.py` — contracts, Scheduler, ATO, TTS registry |
| P17.5 Architecture Audit | `architecture_audit.py` — Single Owner / LOCK / imports / Scheduler |
| P17.6 Docs Audit | `docs_audit.py` + `docs/CHANGELOG_RELEASE.md` |
| P17.7 Certificate | `certificate.py` + `scripts/run_p17_certify.py` |

## Commands

```bash
make certify
python scripts/run_p17_certify.py --promote
```

## Tests

`tests/test_release_governance_p17.py`

## Governance rule

После P0–P17 архитектурный каркас считается закрытым. Дальнейшая работа — функциональное развитие продукта (TTS, UX, контент), а не перестройка основы. Любое изменение frozen-конфига — отдельный PR.
