#!/usr/bin/env python3
"""Golden dataset runner for Timing Agent v1.0."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.timing_agent.agent import TimingAgent

GOLDEN_SEGMENTS = [
    {
        "text": "He said George Smith went home because it was very late.",
        "semantic_text": (
            "Он сказал что George Smith пошёл домой в настоящее время "
            "потому что было очень поздно."
        ),
        "start": 0,
        "end": 1500,
    },
    {
        "text": "Yes.",
        "semantic_text": "Да.",
        "start": 1500,
        "end": 5000,
    },
    {
        "text": "Hello world.",
        "semantic_text": "Привет мир.",
        "start": 5000,
        "end": 7000,
    },
]


def run_golden(output_dir: Path | None = None) -> dict:
    out = output_dir or (ROOT / "output" / "golden_timing")
    out.mkdir(parents=True, exist_ok=True)

    project_uuid = str(uuid.uuid4())
    manifest = {
        "project_uuid": project_uuid,
        "planner_version": "3.0",
        "source_lang": "en",
        "target_lang": "ru",
        "capability_matrix": {"llm": False},
    }

    segments = [
        {
            "index": i,
            "text": row["text"],
            "translated_text": row["semantic_text"],
            "semantic_text": row["semantic_text"],
            "start": row["start"],
            "end": row["end"],
        }
        for i, row in enumerate(GOLDEN_SEGMENTS)
    ]

    agent = TimingAgent(output_dir=out, use_llm=False)
    state = {"segments": segments, "semantic_agent_status": "success"}
    result = agent.run(manifest, state, "golden-timing-run")

    checks = []
    for seg in result.updated_state["segments"]:
        checks.append(
            {
                "index": seg["index"],
                "semantic_text": seg["semantic_text"],
                "timing_text": seg["timing_text"],
                "ok": bool(str(seg.get("timing_text") or "").strip()),
            }
        )

    summary = {
        "project_uuid": project_uuid,
        "status": result.status,
        "avg_scores": result.metrics.get("avg_scores"),
        "rule_rewrite_used": result.metrics.get("rule_rewrite_used"),
        "llm_rewrite_used": result.metrics.get("llm_rewrite_used"),
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "timing_report_path": result.updated_state.get("timing_report_path"),
    }

    summary_path = out / "manifests" / project_uuid / "golden_timing_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden Timing Agent test")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    pytest = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_timing_agent.py", "-q"],
        cwd=ROOT,
    )
    if pytest.returncode != 0:
        return pytest.returncode

    import_app = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=ROOT,
    )
    if import_app.returncode != 0:
        return import_app.returncode

    summary = run_golden(args.output)
    return 0 if summary.get("all_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
