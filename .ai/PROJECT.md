# VideoMonster V2 — Project Brain

## Vision

VideoMonster V2 (TubeDub) is a high-scale intelligent AI dubbing platform — not just an
application, but a modular, self-analyzing platform for long-term evolution without
technical debt.

## Platform Stack (10 Stages)

| Stage | Component |
|-------|-----------|
| 1 | Event Bus |
| 2 | AI Orchestrator |
| 3 | LLM Dispatcher |
| 4 | Pipeline Engine + Adaptive Chunking |
| 5 | Auto Recovery + Micro Validator |
| 6 | AI Memory + Semantic Cache |
| 7 | Performance Optimizer + Hardware Profiler |
| 8 | Monitoring Center + Analytics |
| 9 | Developer SDK + Plugin System |
| 10 | Autonomous Development Platform (Project Brain) |

## Core Principle

**Never modify dubbing quality algorithms.** Extend via plugins, wrappers, and memory layers.

## Entry Points

- `app.py` — Flask application
- `core/event_pipeline.py` — pipeline entry
- `plugins/` — extension registry
- `.ai/` — Project Brain (this directory)

## AI Tools

All AI development tools work exclusively through Project Brain and `DevAssistant`:

```python
from core.dev_assistant import get_dev_assistant
assistant = get_dev_assistant()
assistant.analyze()
assistant.plan("Add new TTS plugin")
```

## Human-in-the-Loop (§14)

AI analyzes, proposes, explains, and forecasts. **Developer always decides.**
