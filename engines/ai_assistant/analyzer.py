"""AI Assistant — rule-based analysis of platform traces (TZ Etap 9)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _load_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("records") or [])
    except Exception:
        return []


def analyze_trace_file(trace_path: str) -> list[dict[str, Any]]:
    """Return structured issues: where, why, what, fix."""
    records = _load_trace(Path(trace_path))
    issues: list[dict[str, Any]] = []

    for rec in records:
        stage = str(rec.get("stage") or "")
        err = rec.get("error")
        if err:
            issues.append(
                {
                    "where": stage,
                    "why": "stage_error",
                    "what": str(err),
                    "fix": "Check logs and retry; verify input source and models.",
                    "severity": "error",
                }
            )

        inp = str(rec.get("input_preview") or "")
        out = str(rec.get("output_preview") or "")
        if "live.translate" in stage and re.search(r"молодш", out, re.I) and "ru" in stage:
            pass
        if "live.translate" in stage and re.search(r"[іїє]", out) and not re.search(r"[іїє]", inp):
            issues.append(
                {
                    "where": stage,
                    "why": "mixed_language_leak",
                    "what": "Ukrainian characters in RU output",
                    "fix": "Enable entity polish / check target language route.",
                    "severity": "warn",
                }
            )

        dur = float(rec.get("duration_ms") or 0)
        if "live.stt" in stage and dur > 8000:
            issues.append(
                {
                    "where": stage,
                    "why": "latency",
                    "what": f"STT took {dur:.0f}ms",
                    "fix": "Use smaller STT model (VM_LIVE_STT_MODEL=tiny) or shorter chunks.",
                    "severity": "warn",
                }
            )
        if "live.tts" in stage and dur > 10000:
            issues.append(
                {
                    "where": stage,
                    "why": "latency",
                    "what": f"TTS took {dur:.0f}ms",
                    "fix": "Enable VM_LIVE_SIMULATE_ONLY=1 for subtitles-only or reduce chunk size.",
                    "severity": "warn",
                }
            )

        if not out and inp and "error" not in stage:
            issues.append(
                {
                    "where": stage,
                    "why": "empty_output",
                    "what": "Stage produced no output",
                    "fix": "Verify engine availability and input format.",
                    "severity": "warn",
                }
            )

    return issues


def analyze_session_dir(app_dir: Path, module: str, session_id: str) -> dict[str, Any]:
    path = app_dir / "output" / "dev" / module / f"{module}_{session_id}.json"
    issues = analyze_trace_file(str(path))
    return {
        "module": module,
        "session_id": session_id,
        "trace_path": str(path),
        "issue_count": len(issues),
        "issues": issues,
    }


def analyze_translation_review_segment(
    *,
    source: str,
    translated: str,
    router_reason: str = "",
) -> list[dict[str, Any]]:
    """Lightweight assistant for dub/translate UI."""
    issues: list[dict[str, Any]] = []
    if re.search(r"молодш", translated, re.I) and "ru" in router_reason.lower():
        issues.append(
            {
                "where": "translation",
                "why": "uk_calque",
                "what": "«молодший» in Russian dub",
                "fix": "Apply fix_ru_jr_suffix / re-run naturalizer.",
                "severity": "warn",
            }
        )
    if re.search(r"[а-яё]{3,}", source) is None and re.search(r"[іїє]", translated):
        issues.append(
            {
                "where": "translation",
                "why": "script_leak",
                "what": "Ukrainian letters in non-UK target",
                "fix": "Check MT route and entity restore.",
                "severity": "error",
            }
        )
    return issues
