"""RASM R4 — sync_report.json / .html / .csv + sync_monitor.log."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.rasm.config import RasmSettings, default_settings, load_rasm_settings
from engines.rasm.metrics import SegmentSyncMetrics, analyze_segments, compute_stats

logger = logging.getLogger("tubedub.rasm.sync_monitor")


def _session_dir(app_dir: Path, task_id: str) -> Path:
    safe = Path(task_id).name
    d = Path(app_dir) / "output" / "sessions" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_log_handler(app_dir: Path) -> None:
    log_path = Path(app_dir) / "logs" / "sync_monitor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("tubedub.rasm.sync_monitor")
    for h in root.handlers:
        if getattr(h, "baseFilename", None) and Path(h.baseFilename) == log_path.resolve():
            return
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(fh)
    root.setLevel(logging.INFO)


def build_report_payload(
    task_id: str,
    segments: list[dict[str, Any]],
    *,
    settings: RasmSettings | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = settings or default_settings()
    rows = analyze_segments(segments, settings=cfg)
    stats = compute_stats(rows)
    return {
        "ok": True,
        "task_id": Path(task_id).name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "R5",
        "settings": cfg.to_dict(),
        "stats": stats,
        "segments": [r.to_dict() for r in rows],
        **(extra or {}),
    }


def render_html_report(payload: dict[str, Any]) -> str:
    stats = payload.get("stats") or {}
    rows = payload.get("segments") or []
    colors = {"green": "#22c55e", "yellow": "#eab308", "red": "#ef4444"}

    def _cell(st: str) -> str:
        c = colors.get(st, "#999")
        return f'<td style="background:{c};color:#111;font-weight:600">{st}</td>'

    trs = []
    for r in rows:
        trs.append(
            "<tr>"
            f"<td>{r.get('index')}</td>"
            f"<td>{r.get('segment_id')}</td>"
            f"<td>{r.get('original_start_ms')}–{r.get('original_end_ms')}</td>"
            f"<td>{r.get('dub_start_ms')}–{r.get('dub_end_ms')}</td>"
            f"<td>{r.get('reserve_ms')}</td>"
            f"<td>{r.get('overflow_ms')}</td>"
            f"<td>{r.get('early_ms')}</td>"
            f"<td>{r.get('late_ms')}</td>"
            f"<td>{r.get('gap_to_next_ms')}</td>"
            f"<td>{r.get('overlap_with_next')}</td>"
            f"{_cell(str(r.get('status')))}"
            f"<td>{', '.join(r.get('flags') or [])}</td>"
            f"<td>{r.get('sync_qc') or ''}</td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>RASM Sync Report — {payload.get('task_id')}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;background:#0f1115;color:#e5e7eb}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{border:1px solid #333;padding:6px 8px;text-align:left}}
th{{background:#1f2937}}
.stats span{{margin-right:16px}}
</style></head><body>
<h1>RASM Sync Report</h1>
<p>Task: <b>{payload.get('task_id')}</b> · {payload.get('generated_at')}</p>
<div class="stats">
<span>Total: {stats.get('segments_total')}</span>
<span style="color:#22c55e">Green: {stats.get('green')}</span>
<span style="color:#eab308">Yellow: {stats.get('yellow')}</span>
<span style="color:#ef4444">Red: {stats.get('red')}</span>
<span>Avg reserve: {stats.get('avg_reserve_ms')} ms</span>
<span>Max overflow: {stats.get('max_overflow_ms')} ms</span>
</div>
<table>
<thead><tr>
<th>#</th><th>ID</th><th>Original</th><th>Dub</th><th>Reserve</th><th>Overflow</th>
<th>Early</th><th>Late</th><th>Gap</th><th>Overlap</th><th>Status</th><th>Flags</th><th>QC</th>
</tr></thead>
<tbody>
{''.join(trs)}
</tbody></table>
</body></html>
"""


def write_csv_report(payload: dict[str, Any]) -> str:
    buf = io.StringIO()
    fields = [
        "index", "segment_id",
        "original_start_ms", "original_end_ms", "original_duration_ms",
        "dub_start_ms", "dub_end_ms", "dub_duration_ms",
        "reserve_ms", "overflow_ms", "early_ms", "late_ms",
        "gap_to_next_ms", "overlap_with_next",
        "duration_overflow_ms", "placement_overflow_ms",
        "status", "flags", "fitted_file_ok", "sync_qc",
    ]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in payload.get("segments") or []:
        row = dict(r)
        row["flags"] = "|".join(row.get("flags") or [])
        w.writerow(row)
    return buf.getvalue()


def write_sync_reports(
    task_id: str,
    segments: list[dict[str, Any]],
    *,
    app_dir: Path | None = None,
    settings: RasmSettings | None = None,
) -> dict[str, Any]:
    """Write json/html/csv under output/sessions/{task_id}/ and append log."""
    root = Path(app_dir) if app_dir else Path(__file__).resolve().parents[2]
    cfg = settings or load_rasm_settings(root)
    payload = build_report_payload(task_id, segments, settings=cfg)
    sess = _session_dir(root, task_id)

    json_path = sess / "sync_report.json"
    html_path = sess / "sync_report.html"
    csv_path = sess / "sync_report.csv"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html_report(payload), encoding="utf-8")
    csv_path.write_text(write_csv_report(payload), encoding="utf-8")

    _ensure_log_handler(root)
    stats = payload.get("stats") or {}
    logger.info(
        "task=%s total=%s green=%s yellow=%s red=%s max_overflow=%s",
        payload.get("task_id"),
        stats.get("segments_total"),
        stats.get("green"),
        stats.get("yellow"),
        stats.get("red"),
        stats.get("max_overflow_ms"),
    )
    for r in payload.get("segments") or []:
        if r.get("status") == "red":
            logger.warning(
                "OVERFLOW/RED seg=%s overflow=%s early=%s late=%s flags=%s",
                r.get("segment_id"),
                r.get("overflow_ms"),
                r.get("early_ms"),
                r.get("late_ms"),
                r.get("flags"),
            )

    return {
        "ok": True,
        "payload": payload,
        "paths": {
            "json": str(json_path),
            "html": str(html_path),
            "csv": str(csv_path),
            "log": str(root / "logs" / "sync_monitor.log"),
        },
    }


def load_sync_report(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
