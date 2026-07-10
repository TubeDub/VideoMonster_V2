# Autonomous Development Platform — Stage 10 (TZ #10)

## Goal

Transform VideoMonster from an application into a self-analyzing platform that
helps developers evolve the project without accumulating technical debt.

**Principle:** VideoMonster helps create itself. AI proposes — developer decides.

## Project Brain (§1)

Single source of knowledge in `.ai/`:

| File | Purpose |
|------|---------|
| `PROJECT.md` | Vision, stack, principles |
| `ARCHITECTURE.md` | Auto-generated module map |
| `ROADMAP.md` | Completed and future stages |
| `CODING_RULES.md` | Architecture and code standards |
| `UX_RULES.md` | User vs developer mode rules |
| `PERFORMANCE.md` | Performance guidelines + live plan |
| `CHANGELOG.md` | Change history |
| `DECISIONS.md` | Architecture Decision Records |
| `MEMORY.md` | Platform memory overview |
| `KNOWN_ISSUES.md` | Auto-scanned technical debt |

View: `/dev/brain` (developer mode)

## Modules

| Module | Role |
|--------|------|
| `core/architecture_engine.py` | Structure, deps, rules (§2) |
| `core/change_impact.py` | Pre-change risk analysis (§3) |
| `core/code_reviewer.py` | Post-change static review (§4) |
| `core/refactoring_advisor.py` | Refactoring suggestions (§5) |
| `core/doc_sync.py` | Auto documentation sync (§6) |
| `core/task_planner.py` | Task breakdown (§7) |
| `core/technical_debt.py` | Debt monitor (§8) |
| `core/recommendation_engine.py` | Aggregated recommendations (§10) |
| `core/knowledge_base.py` | Best practices DB (§12) |
| `core/development_history.py` | Change history DB (§11) |
| `core/dev_assistant.py` | Unified API (§13) |

## DevAssistant API (§13)

```python
from core.dev_assistant import get_dev_assistant

a = get_dev_assistant()
a.analyze()       # Full project analysis
a.plan(task)      # Development plan
a.review(files)   # Code review
a.optimize()      # Recommendations
a.document()      # Sync .ai/ docs
a.test()          # Run pytest
a.explain(topic)  # Knowledge lookup
a.estimate(task)  # Complexity estimate
a.pre_change(files)   # Pre-change workflow
a.post_change(files)  # Post-change workflow
a.self_diagnose()     # After each film (§9)
```

## Human-in-the-Loop (§14)

| AI can | AI cannot |
|--------|-----------|
| Analyze | Auto-modify code |
| Propose | Auto-change architecture |
| Explain | Auto-update dependencies |
| Forecast | Auto-delete code |

All changes require developer approval.

## HTTP API

```
GET  /api/assistant/status
GET  /api/assistant/analyze
POST /api/assistant/plan          {"task": "..."}
POST /api/assistant/review        {"files": [...]}
GET  /api/assistant/optimize
POST /api/assistant/document
POST /api/assistant/pre-change    {"files": [...], "description": "..."}
POST /api/assistant/explain       {"topic": "..."}
POST /api/assistant/estimate      {"task": "..."}
GET  /api/assistant/brain/{file}
GET  /api/assistant/debt
```

## Storage

| DB | Path |
|----|------|
| Development History | `data/development/development_history.db` |
| Knowledge Base | `data/knowledge/knowledge_base.db` |

## Integration

After each film (`event_pipeline` wrapper):
1. Performance Optimizer `record_film()`
2. Monitoring Center `finalize_project()`
3. DevAssistant `self_diagnose()`

## Future Scalability (§15)

Architecture interfaces support (not yet implemented):
- Distributed processing across machines
- Local + cloud AI hybrid
- Team/corporate editions
- Web API, mobile clients
- Automatic test stands
- New AI models via plugins only

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `VM_DEV_ASSISTANT` | `1` | Enable DevAssistant |
| `VM_DEV_HISTORY_DIR` | `data/development` | History DB |
| `VM_KB_DIR` | `data/knowledge` | Knowledge base |

## Tests

```bash
python -m pytest tests/test_dev_platform.py -q
```

## 10-Stage Platform Complete

VideoMonster V2 is now a modular, event-driven, self-monitoring, self-optimizing,
plugin-extensible, self-analyzing AI dubbing platform ready for long-term evolution.
