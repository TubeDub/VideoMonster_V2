"""Auto-generated work reports — output/reports/WORK_REPORT_YYYYMMDD_HHMM.txt"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any


def write_work_report(
    app_dir: Path,
    *,
    task_title: str,
    discovered: list[str],
    root_cause: str,
    changes: list[str],
    files_changed: list[str],
    functions_changed: list[str],
    tests_run: list[str],
    test_results: dict[str, str],
    remaining_checks: list[str],
    limitations: list[str],
    next_actions: list[str],
    fixed: list[str],
    not_fixed: list[str],
    status: str,
) -> str:
    """status: READY | WARNING | ERROR"""
    reports_dir = app_dir / "output" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M", time.localtime())
    path = reports_dir / f"WORK_REPORT_{stamp}.txt"

    def _section(title: str, items: list[str]) -> str:
        lines = [title]
        if items:
            lines.extend(f"- {x}" for x in items)
        else:
            lines.append("- (none)")
        return "\n".join(lines)

    body = "\n\n".join(
        [
            f"TASK: {task_title}",
            _section("DISCOVERED", discovered),
            f"ROOT CAUSE:\n{root_cause or '(unknown)'}",
            _section("CHANGES", changes),
            _section("FILES CHANGED", files_changed),
            _section("FUNCTIONS ADDED/MODIFIED", functions_changed),
            _section("TESTS RUN", tests_run),
            "TEST RESULTS:\n"
            + "\n".join(f"- {name}: {result}" for name, result in test_results.items())
            or "- (none)",
            _section("REMAINING CHECKS", remaining_checks),
            _section("KNOWN LIMITATIONS", limitations),
            _section("NEXT RECOMMENDED ACTIONS", next_actions),
            "SUMMARY",
            _section("FIXED", fixed),
            _section("NOT FIXED", not_fixed),
            f"PROJECT STATUS: {status.upper()}",
        ]
    )
    path.write_text(body + "\n", encoding="utf-8")
    latest = reports_dir / "WORK_REPORT_LATEST.txt"
    latest.write_text(body + "\n", encoding="utf-8")
    return str(path)
