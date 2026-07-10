"""Report exporter — diagnostic reports in ZIP / JSON / HTML / PDF (TZ #8 §13)."""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any


def export_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def export_html(data: dict[str, Any], *, title: str = "VideoMonster Diagnostic Report") -> bytes:
  """Generate a self-contained HTML diagnostic report."""
  generated = time.strftime("%Y-%m-%d %H:%M:%S")
  project = data.get("project_id") or data.get("dashboard", {}).get("project_id") or "—"
  progress = data.get("dashboard", {}).get("progress_percent", 0)
  bottleneck = data.get("bottleneck") or {}
  diagnostics = data.get("diagnostics") or {}
  ai_report = data.get("ai_report") or {}

  def _rows(items: list[dict], keys: tuple[str, ...]) -> str:
      if not items:
          return "<tr><td colspan='4'>No data</td></tr>"
      rows = []
      for item in items:
          cells = "".join(f"<td>{item.get(k, '')}</td>" for k in keys)
          rows.append(f"<tr>{cells}</tr>")
      return "\n".join(rows)

  stage_rows = _rows(
      bottleneck.get("stages") or [],
      ("label", "percent", "duration_s", "avg_wait_s"),
  )
  issue_rows = _rows(
      diagnostics.get("issues") or [],
      ("severity", "category", "message", "detail"),
  )
  rec_rows = ""
  for rec in ai_report.get("summary") or bottleneck.get("recommendations") or []:
      rec_rows += (
          f"<div class='rec'><strong>{rec.get('title') or rec.get('cause', '')}</strong>"
          f"<p>{rec.get('detail', '')}</p>"
          f"<p class='action'>→ {rec.get('recommendation') or rec.get('action', '')}</p></div>"
      )

  html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<title>{title}</title>
<style>
body{{font-family:Inter,sans-serif;background:#0d0d12;color:#e2e8f0;padding:24px;max-width:960px;margin:0 auto}}
h1{{font-size:22px}} h2{{font-size:16px;margin-top:24px;color:#94a3b8}}
.card{{background:#1a1a2e;border:1px solid #2d2d44;border-radius:8px;padding:16px;margin:12px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
td,th{{padding:8px;border-bottom:1px solid #2d2d44;text-align:left}}
th{{color:#94a3b8}} .rec{{margin:8px 0;padding:8px;background:#12121c;border-radius:6px}}
.action{{color:#3ecf8e;font-size:12px}} .meta{{color:#64748b;font-size:12px}}
.bar{{height:8px;background:#2d2d44;border-radius:4px;overflow:hidden;margin:4px 0}}
.fill{{height:100%;background:#6366f1}}
</style></head><body>
<h1>📊 {title}</h1>
<p class="meta">Generated: {generated} · Project: {project} · Progress: {progress:.0f}%</p>

<div class="card">
<h2>Pipeline Bottleneck</h2>
<table><tr><th>Stage</th><th>%</th><th>Duration (s)</th><th>Avg Wait (s)</th></tr>
{stage_rows}</table>
</div>

<div class="card">
<h2>Diagnostics</h2>
<table><tr><th>Severity</th><th>Category</th><th>Message</th><th>Detail</th></tr>
{issue_rows}</table>
</div>

<div class="card"><h2>Recommendations</h2>{rec_rows or '<p>No recommendations</p>'}</div>

<div class="card"><h2>Resources</h2>
<pre style="font-size:12px">{json.dumps(data.get('resources') or {}, indent=2)}</pre>
</div>
</body></html>"""
  return html.encode("utf-8")


def export_pdf(data: dict[str, Any], *, title: str = "VideoMonster Report") -> bytes:
    """Minimal PDF without external dependencies."""
    lines: list[str] = [
        title,
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "=== Bottleneck ===",
    ]
    for s in (data.get("bottleneck") or {}).get("stages") or []:
        lines.append(f"  {s.get('label', '?')}: {s.get('percent', 0):.0f}%")
    lines.append("")
    lines.append("=== Diagnostics ===")
    for issue in (data.get("diagnostics") or {}).get("issues") or []:
        lines.append(f"  [{issue.get('severity')}] {issue.get('message')}")
    lines.append("")
    lines.append("=== Recommendations ===")
    for rec in (data.get("ai_report") or {}).get("summary") or []:
        lines.append(f"  {rec.get('cause', '')}: {rec.get('recommendation', '')}")

    return _minimal_pdf(lines)


def export_zip(data: dict[str, Any], *, title: str = "diagnostic_report") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.json", export_json(data))
        zf.writestr("report.html", export_html(data, title=title))
        zf.writestr("report.pdf", export_pdf(data, title=title))
        timeline = data.get("timeline") or []
        if timeline:
            zf.writestr(
                "timeline.json",
                json.dumps(timeline, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        logs = data.get("logs") or []
        if logs:
            zf.writestr("logs.txt", "\n".join(str(l) for l in logs).encode("utf-8"))
    return buf.getvalue()


def _minimal_pdf(lines: list[str]) -> bytes:
    """Build a valid minimal single-page PDF with text lines."""
    content_lines = ["BT", "/F1 10 Tf", "50 750 Td"]
    for i, line in enumerate(lines[:60]):
        safe = (
            str(line)
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .encode("ascii", "replace")
            .decode("ascii")
        )
        if i == 0:
            content_lines.append(f"({safe}) Tj")
        else:
            content_lines.append("0 -14 Td")
            content_lines.append(f"({safe}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines)
    stream_bytes = stream.encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        f"4 0 obj<< /Length {len(stream_bytes)} >>stream\n".encode()
        + stream_bytes
        + b"\nendstream endobj\n"
    )
    objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")

    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(pdf.tell())
        pdf.write(obj)
    xref_pos = pdf.tell()
    pdf.write(f"xref\n0 {len(offsets)}\n".encode())
    pdf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.write(f"{off:010d} 00000 n \n".encode())
    pdf.write(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return pdf.getvalue()


def save_report(
    data: dict[str, Any],
    output_dir: str | Path,
    *,
    fmt: str = "zip",
    title: str = "diagnostic_report",
) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    if fmt == "json":
        path = out / f"{title}_{ts}.json"
        path.write_bytes(export_json(data))
    elif fmt == "html":
        path = out / f"{title}_{ts}.html"
        path.write_bytes(export_html(data, title=title))
    elif fmt == "pdf":
        path = out / f"{title}_{ts}.pdf"
        path.write_bytes(export_pdf(data, title=title))
    else:
        path = out / f"{title}_{ts}.zip"
        path.write_bytes(export_zip(data, title=title))
    return path
