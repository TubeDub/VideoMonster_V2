"""Language_Report.txt — v2 detailed report."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any


def write_language_report(meta: dict[str, Any], *, app_dir: Path | None = None) -> str:
    base = app_dir or Path(__file__).resolve().parent.parent.parent
    reports = base / "output" / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d_%H%M", time.localtime())
    path = reports / f"Language_Report_{stamp}.txt"
    latest = reports / "Language_Report.txt"
    legacy = reports / "LANGUAGE_INTELLIGENCE_REPORT.txt"

    applied = meta.get("applied_details") or []
    rejected = meta.get("rejected_details") or []
    suggestions = meta.get("suggestion_details") or []
    err_freq = meta.get("error_frequency") or {}

    conf_values = [float(x.get("confidence") or 0) for x in applied + rejected]
    avg_conf = round(sum(conf_values) / len(conf_values), 3) if conf_values else 0.0

    lines = [
        "LANGUAGE INTELLIGENCE REPORT v2",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Task: {meta.get('task_id', '')}",
        f"Mode: {'Analysis Only' if meta.get('analysis_only') else 'Active'}",
        f"Fast mode: {meta.get('fast_mode', False)}",
        "",
        "SUMMARY",
        f"- Segments: {meta.get('segments', 0)}",
        f"- Applied fixes: {meta.get('fixes_applied', 0)}",
        f"- Rejected fixes: {meta.get('fixes_rejected', 0)}",
        f"- Suggestions only: {meta.get('suggestions', 0)}",
        f"- Segments changed: {meta.get('changed', 0)}",
        f"- Avg Naturalness Score: {meta.get('avg_naturalness', 100)}",
        f"- Avg confidence (fixes): {avg_conf}",
        f"- Elapsed sec: {meta.get('elapsed_sec', 0)}",
        f"- Avg ms/segment: {meta.get('avg_ms_per_segment', 0)}",
        "",
        "MOST COMMON ERROR TYPES",
    ]
    if err_freq:
        for code, n in sorted(err_freq.items(), key=lambda x: -x[1])[:12]:
            lines.append(f"- {code}: {n}")
    else:
        lines.append("- (none)")

    lines.extend(["", "NEW LEARNED RULES"])
    new_rules = meta.get("new_rules") or []
    if not new_rules:
        lines.append("- (none promoted this job)")
    else:
        for r in new_rules:
            lines.append(
                f"- {r.get('pattern')} → {r.get('replacement')} "
                f"conf={r.get('confidence')} count={r.get('count')}"
            )

    lines.extend(["", "APPLIED FIXES (sample)"])
    for fx in applied[:40]:
        lines.append(
            f"- seg#{fx.get('segment_index')} [{fx.get('code')}] "
            f"\"{fx.get('before')}\" → \"{fx.get('after')}\" "
            f"conf={int(float(fx.get('confidence', 0)) * 100)}% "
            f"src={fx.get('source')}"
        )

    lines.extend(["", "REJECTED FIXES (sample)"])
    for fx in rejected[:25]:
        reasons = ",".join(fx.get("block_reasons") or []) or "semantic/confidence"
        lines.append(
            f"- seg#{fx.get('segment_index')} [{fx.get('code')}] "
            f"reason={reasons} conf={int(float(fx.get('confidence', 0)) * 100)}%"
        )

    if suggestions:
        lines.extend(["", "SUGGESTIONS (Analysis / low confidence)"])
        for fx in suggestions[:25]:
            lines.append(
                f"- seg#{fx.get('segment_index')} would: \"{fx.get('would_be', '')[:80]}\" "
                f"conf={int(float(fx.get('confidence', 0)) * 100)}%"
            )

    lines.extend(
        [
            "",
            "LIMITATIONS",
            "- Module does not replace Marian/Naturalizer.",
            "- Semantic check is heuristic (numbers, negation, brands, length).",
            "- Learned rules require ≥5 occurrences and ≥85% success.",
            "- Default OFF: VM_LANGUAGE_INTELLIGENCE=1 to enable.",
            "- Analysis only: VM_LANGUAGE_INTELLIGENCE_ANALYSIS=1",
        ]
    )

    body = "\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")
    latest.write_text(body, encoding="utf-8")
    legacy.write_text(body, encoding="utf-8")
    return str(latest)


def write_report(meta: dict[str, Any], *, app_dir: Path | None = None, **_: Any) -> str:
    """Backward-compatible alias."""
    return write_language_report(meta, app_dir=app_dir)
