"""Detailed translate session logs for the «Перевод» section."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def logs_dir(app_dir: Path) -> Path:
    d = app_dir / "output" / "dev" / "translate_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_pointer(app_dir: Path) -> Path:
    return app_dir / "output" / "dev" / "translate_latest.json"


def save_session_log(
    app_dir: Path,
    session_id: str,
    *,
    source_lang: str,
    target_lang: str,
    source_segments: list[str],
    translated_segments: list[str],
    audits: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
    trace_log: str = "",
) -> Path:
    """Persist full session log; update latest pointer."""
    sid = str(session_id or "").strip()
    if not sid:
        return logs_dir(app_dir) / "empty.json"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "session_id": sid,
        "timestamp": ts,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "segment_count": len(source_segments),
        "source_segments": source_segments,
        "translated_segments": translated_segments,
        "audits": audits,
        "meta": meta or {},
        "trace_log": trace_log,
    }

    path = logs_dir(app_dir) / f"translate_{sid}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    latest_pointer(app_dir).write_text(
        json.dumps({"session_id": sid, "path": str(path), "timestamp": ts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = app_dir / "output" / "dev" / "translate_sessions.log"
    line = (
        f"{ts}\tsession={sid}\t{source_lang}->{target_lang}\t"
        f"segments={len(source_segments)}\tfile={path.name}\n"
    )
    with summary.open("a", encoding="utf-8") as f:
        f.write(line)

    return path


def get_latest_log(app_dir: Path) -> dict[str, Any]:
    ptr = latest_pointer(app_dir)
    if not ptr.is_file():
        return {"ok": False, "error": "no_logs"}
    try:
        info = json.loads(ptr.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "corrupt_pointer"}
    path = Path(info.get("path") or "")
    if not path.is_file():
        sid = info.get("session_id", "")
        path = logs_dir(app_dir) / f"translate_{sid}.json"
    if not path.is_file():
        return {"ok": False, "error": "log_not_found"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "session_id": data.get("session_id", ""),
        "path": str(path),
        "filename": path.name,
        "timestamp": data.get("timestamp", ""),
        "data": data,
    }


def build_text_report(app_dir: Path, session_id: str) -> str:
    from engines.translate_lab import build_inspector_report
    from engines.translation_inspector import export_inspector_text

    sid = str(session_id or "").strip()
    latest = get_latest_log(app_dir)
    session_data: dict[str, Any] = {}
    if latest.get("ok") and latest.get("session_id") == sid:
        session_data = latest.get("data") or {}
    else:
        p = logs_dir(app_dir) / f"translate_{sid}.json"
        if p.is_file():
            session_data = json.loads(p.read_text(encoding="utf-8"))

    lines = [
        f"=== TubeDub Translate Log ===",
        f"session={sid}",
        f"pair={session_data.get('source_lang', '?')}->{session_data.get('target_lang', '?')}",
        f"segments={session_data.get('segment_count', 0)}",
        f"timestamp={session_data.get('timestamp', '')}",
        "",
    ]

    meta = session_data.get("meta") or {}
    if meta:
        lines.append("--- meta ---")
        for k, v in meta.items():
            if k != "translation_trace_log":
                lines.append(f"{k}={v}")
        lines.append("")

    trace = session_data.get("trace_log") or meta.get("translation_trace_log") or ""
    if trace:
        lines.extend(["--- trace ---", str(trace), ""])

    try:
        insp = build_inspector_report(sid)
        if insp.get("segments"):
            lines.append(export_inspector_text(insp))
    except Exception as e:
        lines.append(f"(inspector unavailable: {e})")

    return "\n".join(lines).strip() + "\n"


def clear_all_logs(app_dir: Path) -> dict[str, Any]:
    removed = 0
    d = logs_dir(app_dir)
    for p in d.glob("translate_*.json"):
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    for name in ("translate_latest.json", "translate_sessions.log"):
        p = app_dir / "output" / "dev" / name
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    return {"ok": True, "removed": removed}


def open_path_in_shell(path: Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "not_found"}
    try:
        if os.name == "nt":
            if p.is_file():
                os.startfile(str(p))  # noqa: S606
            else:
                subprocess.Popen(["explorer", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

