"""STRESS_TEST_REPORT.html / .txt generation."""

from __future__ import annotations

import html
import time
from pathlib import Path
from typing import Any

from engines.stress_test.config import reports_dir


def write_stress_reports(batch: dict[str, Any], *, app_dir: Path) -> dict[str, str]:
    reports = reports_dir(app_dir)
    txt_path = reports / "STRESS_TEST_REPORT.txt"
    html_path = reports / "STRESS_TEST_REPORT.html"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")

    results = batch.get("results") or []
    summary = batch.get("summary") or {}
    total = int(batch.get("total") or len(results))
    passed = int(batch.get("passed") or 0)
    failed = int(batch.get("failed") or max(0, total - passed))

    txt_lines = [
        "STRESS TEST CENTER — TubeDub",
        f"Generated: {stamp}",
        f"Version: {batch.get('version', '')}",
        f"Batch: {batch.get('batch_id', '')}",
        "",
        "SUMMARY",
        f"- Tests total: {total}",
        f"- Passed: {passed}",
        f"- Failed: {failed}",
        f"- Avg duration (sec): {summary.get('avg_duration_sec', 0)}",
        f"- Total elapsed (sec): {summary.get('elapsed_sec', 0)}",
        f"- Avg Quality Score: {summary.get('avg_quality', 'n/a')}",
        "",
        "RESULTS",
    ]

    for r in results:
        status = "PASS" if r.get("passed") else "FAIL"
        txt_lines.append(
            f"- [{status}] {r.get('video')} "
            f"quality={r.get('avg_quality')} "
            f"time={r.get('duration_sec')}s "
            f"issues={len(r.get('issues') or [])}"
        )
        for issue in (r.get("issues") or [])[:8]:
            txt_lines.append(
                f"    · {issue.get('severity')} {issue.get('code')} "
                f"seg={issue.get('segment', '')} {issue.get('detail', '')}"
            )
        for lp in r.get("log_paths") or []:
            txt_lines.append(f"    log: {lp}")

    txt_lines.extend(
        [
            "",
            "STAGE CHECKLIST (last run aggregate)",
        ]
    )
    if results:
        stages = results[-1].get("stages") or {}
        for k, v in stages.items():
            txt_lines.append(f"- {k}: {v}")

    txt_body = "\n".join(txt_lines) + "\n"
    txt_path.write_text(txt_body, encoding="utf-8")

    rows_html = []
    for r in results:
        cls = "pass" if r.get("passed") else "fail"
        issues_html = "<br>".join(
            html.escape(
                f"{i.get('severity')} {i.get('code')} "
                f"#{i.get('segment', '')} {str(i.get('detail', ''))[:60]}"
            )
            for i in (r.get("issues") or [])[:6]
        )
        logs_html = "<br>".join(
            html.escape(str(lp)) for lp in (r.get("log_paths") or [])[:4]
        )
        rows_html.append(
            f"<tr class='{cls}'><td>{html.escape(str(r.get('video')))}</td>"
            f"<td>{'✅' if r.get('passed') else '❌'}</td>"
            f"<td>{r.get('avg_quality', '—')}</td>"
            f"<td>{r.get('duration_sec', 0)}</td>"
            f"<td>{issues_html or '—'}</td>"
            f"<td>{logs_html or '—'}</td></tr>"
        )

    html_body = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<title>Stress Test Report — TubeDub</title>
<style>
body {{ font-family: Inter, Segoe UI, sans-serif; background:#0f1117; color:#e8eaed; padding:24px; }}
h1 {{ font-size:22px; }}
.summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin:16px 0; }}
.card {{ background:#1a1d27; border:1px solid #2a2f3d; border-radius:10px; padding:12px; }}
.card strong {{ display:block; font-size:22px; }}
table {{ width:100%; border-collapse:collapse; margin-top:16px; font-size:13px; }}
th, td {{ border:1px solid #2a2f3d; padding:8px; vertical-align:top; }}
th {{ background:#1a1d27; text-align:left; }}
tr.pass td:nth-child(2) {{ color:#4ade80; }}
tr.fail td:nth-child(2) {{ color:#f87171; }}
.meta {{ color:#9aa0a6; font-size:12px; }}
</style>
</head>
<body>
<h1>🧪 Stress Test Center</h1>
<p class="meta">Generated {html.escape(stamp)} · Version {html.escape(str(batch.get('version', '')))} · Batch {html.escape(str(batch.get('batch_id', '')))}</p>
<div class="summary">
  <div class="card"><span>Tests</span><strong>{total}</strong></div>
  <div class="card"><span>Passed</span><strong>{passed}</strong></div>
  <div class="card"><span>Failed</span><strong>{failed}</strong></div>
  <div class="card"><span>Avg time</span><strong>{summary.get('avg_duration_sec', 0)}s</strong></div>
  <div class="card"><span>Avg Quality</span><strong>{summary.get('avg_quality', '—')}</strong></div>
</div>
<table>
<thead><tr><th>Video</th><th>Status</th><th>Quality</th><th>Time</th><th>Issues</th><th>Logs</th></tr></thead>
<tbody>
{''.join(rows_html) if rows_html else '<tr><td colspan="6">No videos in data/stress_tests/</td></tr>'}
</tbody>
</table>
<p class="meta">History: output/stress_history/</p>
</body>
</html>
"""
    html_path.write_text(html_body, encoding="utf-8")
    return {"txt": str(txt_path), "html": str(html_path)}
