#!/usr/bin/env python3
"""Golden dataset runner for Quality Agent v1.0."""

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

from unittest.mock import patch

from engines.ai_core.quality_agent.agent import QualityAgent

GOLDEN_SEGMENTS = [
    {
        "text": "He said that George Smith went home on 12.05.2024.",
        "grammar_text": "Он сказал, что George Smith пошёл домой 12.05.2024.",
        "start": 0,
        "end": 3500,
    },
    {
        "text": "Wow!!! That is amazing!",
        "grammar_text": "Вау! Это удивительно!",
        "start": 3500,
        "end": 6000,
    },
    {
        "text": "I am sad because he left.",
        "grammar_text": "Мне грустно, потому что он ушёл.",
        "start": 6000,
        "end": 9000,
    },
]


def run_golden(output_dir: Path | None = None) -> dict:
    out = output_dir or (ROOT / "output" / "golden_quality")
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
            "translated_text": row["grammar_text"],
            "semantic_text": row["grammar_text"],
            "timing_text": row["grammar_text"],
            "grammar_text": row["grammar_text"],
            "start": row["start"],
            "end": row["end"],
        }
        for i, row in enumerate(GOLDEN_SEGMENTS)
    ]

    agent = QualityAgent(output_dir=out)
    state = {"segments": segments, "grammar_agent_status": "success"}
    with patch(
        "engines.ai_core.quality_agent.agent.smart_router.route_and_fix_segment",
        side_effect=lambda seg, *a, **k: (seg, None),
    ):
        result = agent.run(manifest, state, "golden-quality-run")

    checks = []
    for seg in result.updated_state["segments"]:
        checks.append(
            {
                "index": seg["index"],
                "quality_decision": seg.get("quality_decision"),
                "quality_passed": seg.get("quality_passed"),
                "overall_score": (seg.get("quality_scores") or {}).get("overall"),
                "ok": bool(seg.get("quality_passed")),
            }
        )

    summary = {
        "project_uuid": project_uuid,
        "status": result.status,
        "quality_summary": result.metrics.get("summary"),
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "quality_report_path": result.updated_state.get("quality_report_path"),
    }

    summary_path = out / "manifests" / project_uuid / "golden_quality_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return summary


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Golden Quality Agent test")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    pytest = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_quality_agent.py", "-q"],
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
