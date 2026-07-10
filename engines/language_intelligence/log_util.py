"""Logging to output/dev/language_intelligence.log"""

from __future__ import annotations

import time
from pathlib import Path


def log_dir(app_dir: Path | None = None) -> Path:
    base = app_dir or Path(__file__).resolve().parent.parent.parent
    d = base / "output" / "dev"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path(app_dir: Path | None = None) -> Path:
    return log_dir(app_dir) / "language_intelligence.log"


def append_log(lines: list[str], app_dir: Path | None = None) -> str:
    path = log_path(app_dir)
    latest = log_dir(app_dir) / "language_intelligence_latest.log"
    text = "\n".join(lines) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
    latest.write_text(path.read_text(encoding="utf-8")[-500_000:], encoding="utf-8")
    return str(path)


def log_job_start(task_id: str = "", app_dir: Path | None = None) -> None:
    append_log(["START", f"task={task_id}", f"time={time.strftime('%Y-%m-%d %H:%M:%S')}"], app_dir)


def log_segment_fix(
    index: int,
    fix: dict,
    *,
    app_dir: Path | None = None,
) -> None:
    code = fix.get("code", "")
    before = fix.get("before", "")
    after = fix.get("after", "")
    src = fix.get("source", "Language Rule")
    conf = fix.get("confidence", 0)
    lines = [
        f"Segment {index}",
        f'Обнаружено: {code} "{before}"',
        "↓",
        f'Исправлено: "{after}"',
        f"Источник: {src}",
        f"Confidence: {int(float(conf) * 100)}%",
        "",
    ]
    append_log(lines, app_dir)


def log_job_end(meta: dict, app_dir: Path | None = None) -> None:
    append_log(
        [
            "END",
            f"segments={meta.get('segments', 0)}",
            f"changed={meta.get('changed', 0)}",
            f"elapsed_sec={meta.get('elapsed_sec', 0)}",
            "",
        ],
        app_dir,
    )
