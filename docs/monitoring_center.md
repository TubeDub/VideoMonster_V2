# Monitoring Center + Analytics + Diagnostics — Stage 8 (TZ #8)

## Goal

Create a unified intelligent monitoring hub that displays the full system state
in real time, detects bottlenecks, analyses performance, and helps find the root
cause of any error — without turning VideoMonster into a black box.

**Principle:** User and developer can see system state at any moment.

## Modules

| Module | Role |
|--------|------|
| `core/monitoring_center.py` | Central monitoring hub + public API (§1, §16) |
| `core/diagnostics.py` | Automatic health checks (§9) |
| `core/bottleneck_analyzer.py` | Pipeline bottleneck detection (§10) |
| `core/analytics_db.py` | Project history SQLite store (§12) |
| `core/report_exporter.py` | ZIP / JSON / HTML / PDF export (§13) |
| `templates/monitoring.html` | User + Developer dashboard UI |
| `static/js/monitoring.js` | Live polling client |

## Architecture

```
Live state (read-only collectors)
  Orchestrator · Pipeline Engine · LLM Dispatcher
  Performance Monitor · Recovery · AI Memory · Optimizer
        ↓
  MonitoringCenter
    ├─ get_dashboard()      → progress, ETA, agents (§2)
    ├─ get_pipeline()       → stage visualization (§3)
    ├─ get_queues()         → queue monitor (§4)
    ├─ get_resources()      → CPU/GPU/RAM/disk (§5)
    ├─ get_models()         → LLM monitor (§6)
    ├─ get_agents()         → agent monitor (§7)
    ├─ get_timeline()       → event timeline (§8)
    ├─ get_diagnostics()    → health scan (§9)
    ├─ get_bottleneck()     → bottleneck % (§10)
    ├─ finalize_project()   → AI report + self-diagnosis (§11, §17)
    └─ export_report()      → ZIP/JSON/HTML/PDF (§13)

After project (event_pipeline wrapper)
  finalize_project() → analytics.db + optimizer recommendations
```

## Public API (§16)

```python
from core.monitoring_center import get_monitor

mon = get_monitor(app_dir=APP_DIR)
mon.get_pipeline()
mon.get_agents()
mon.get_resources()
mon.get_models()
mon.get_statistics()
mon.export_report(fmt="zip")
mon.get_history()
```

## User vs Developer Mode (§14–§15)

| Mode | URL | Visible data |
|------|-----|-------------|
| User | `/monitoring` | Progress, ETA, stage, warnings, recommendations |
| Developer | `/dev/monitoring` | Full pipeline, agents, LLM, queues, bottleneck, timeline, export |

Developer API endpoints require `is_developer_session()`.

## Diagnostics (§9)

`DiagnosticsCenter.run_full_scan()` detects:

- Stuck tasks (>300s)
- Queue overflow (>90% capacity)
- Memory pressure trend (rising RAM)
- High retry / duplicate processing
- Idle agents during active pipeline

## Bottleneck Analyzer (§10)

Computes per-stage time percentages:

```
Whisper   10%
Translator 62%  ← primary bottleneck
Voice     18%
```

Recommendations forwarded to Performance Optimizer via public `rebalance_for_bottleneck()` API.

## AI Diagnostics Report (§11)

After `finalize_project()`:

```json
{
  "summary": [{
    "title": "Основная причина замедления",
    "cause": "Translator",
    "detail": "62% pipeline time",
    "recommendation": "Increase Translation queue"
  }]
}
```

## Analytics Database (§12)

`data/analytics/analytics.db`:

- `project_runs` — date, duration, models, performance, errors, recommendations, speed
- `timeline_events` — timestamped processing events

## Report Export (§13)

`POST /api/monitor/export` with `{"format": "zip|json|html|pdf"}`

ZIP contains: `report.json`, `report.html`, `report.pdf`, `timeline.json`

## HTTP Endpoints

```
GET  /api/monitor/dashboard?developer=1
GET  /api/monitor/pipeline
GET  /api/monitor/agents
GET  /api/monitor/resources
GET  /api/monitor/models
GET  /api/monitor/queues
GET  /api/monitor/statistics
GET  /api/monitor/timeline
GET  /api/monitor/history
GET  /api/monitor/diagnostics
GET  /api/monitor/developer
POST /api/monitor/export
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VM_MONITORING` | `1` | Enable Monitoring Center |
| `VM_ANALYTICS_DIR` | `data/analytics` | Analytics DB directory |

## What is NOT changed (TZ constraint)

- Event Bus, AI Orchestrator, LLM Dispatcher, Pipeline Engine, Performance Optimizer
- Translation / processing algorithms and quality
- Existing UI (only added `/monitoring` and `/dev/monitoring` pages)

## Tests

```bash
python -m pytest tests/test_monitoring.py -q
```

Coverage: analytics DB, bottleneck analysis, diagnostics scans, monitoring API,
user/developer modes, report export formats, project finalization.
