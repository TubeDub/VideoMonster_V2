# OpenDDF v0.1.0

Open Developer Diagnostic Framework — platform-agnostic Python diagnostic SDK.

## Quick start

```python
from openddf import DiagnosticContext, SnapshotGuard, TimelineTracker

timeline = TimelineTracker()

with DiagnosticContext(run_id="demo-1", output_dir="./output") as ctx:
    ctx.timeline = timeline
    data = {"status": "pending", "token": "secret-value"}
    ctx.register_snapshots(dict(data), dict(data))
    with SnapshotGuard(data, allowed_mutations={"status"}, context_tracker=timeline):
        data["status"] = "ready"  # OK
        data["token"] = "changed"  # raises StageSnapshotIntegrityError
```

On crash, OpenDDF writes `diagnostics/diagnostic_<run_id>.zip` with flat files:
`pipeline.log`, `stacktrace.txt`, `environment.json`, `report.json`, and optional snapshots.

## Modules

| Module | Purpose |
|--------|---------|
| `exceptions` | `DDFError`, `StageSnapshotIntegrityError` |
| `timeline` | `TimelineTracker` |
| `diff` | `DiffAnalyzer` |
| `utils` | `filter_sensitive_data` |
| `environment` | `collect_environment_info` |
| `guard` | `SnapshotGuard` |
| `report` | `RecoveryHintGenerator` |
| `dumper` | `DiagnosticDumper` |
| `__init__` | `DiagnosticContext` orchestrator |

Standard library only in core (pytest for tests).
