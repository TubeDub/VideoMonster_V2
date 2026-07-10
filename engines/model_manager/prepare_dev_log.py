"""Write prepare-phase diagnostics to output/dev/."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_prepare_plan(
    app_dir: Path,
    *,
    source_lang: str,
    target_lang: str,
    plan: Any,
    events: list[str] | None = None,
    job_id: str = "",
) -> str:
    log_dir = app_dir / "output" / "dev"
    log_dir.mkdir(parents=True, exist_ok=True)
    jid = job_id or uuid.uuid4().hex[:12]
    path = log_dir / f"prepare_{jid}.log"
    ts = datetime.now(timezone.utc).isoformat()
    lines = [
        f"=== PREPARE ROUTE PLAN ts={ts} ===",
        f"job_id={jid}",
        f"source_lang={source_lang} target_lang={target_lang}",
        "",
    ]
    if hasattr(plan, "to_dev_log_lines"):
        lines.extend(plan.to_dev_log_lines())
    else:
        lines.append(json.dumps(plan, ensure_ascii=False, indent=2))

    if events:
        lines.extend(["", "=== PREPARE EVENTS ==="])
        lines.extend(events)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    latest = log_dir / "prepare_latest.log"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return str(path)
