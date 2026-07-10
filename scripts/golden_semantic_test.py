#!/usr/bin/env python3
"""Golden dataset runner for Semantic Agent v1.0."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.semantic_agent.agent import SemanticAgent
from engines.ai_core.semantic_agent.validators.meaning_validator import validate_meaning

GOLDEN_SEGMENTS = [
    {
        "text": "John Smith visited Berlin on 12.05.2024 with 100 guests.",
        "translated_text": "John Smith посетил Berlin 12.05.2024 с 100 guests.",
    },
    {
        "text": "Wow!!! That is amazing!",
        "translated_text": "Вау!!! Это удивительно!",
    },
    {
        "text": "I am sad because he left.",
        "translated_text": "Мне грустно потому что он ушёл.",
    },
]


def run_golden(output_dir: Path | None = None) -> dict:
    out = output_dir or (ROOT / "output" / "golden_semantic")
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
            "translated_text": row["translated_text"],
            "start": i * 2000,
            "end": (i + 1) * 2000,
        }
        for i, row in enumerate(GOLDEN_SEGMENTS)
    ]

    agent = SemanticAgent(output_dir=out)

    state = {"segments": segments, "translation_agent_status": "success"}
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(manifest, state, "golden-semantic-run")

    checks = []
    for seg in result.updated_state["segments"]:
        meaning = validate_meaning(
            seg["text"],
            seg["translated_text"],
            seg["semantic_text"],
        )
        checks.append(
            {
                "index": seg["index"],
                "semantic_text": seg["semantic_text"],
                "meaning_score": meaning.score,
                "ok": meaning.ok,
            }
        )

    summary = {
        "project_uuid": project_uuid,
        "status": result.status,
        "avg_scores": result.metrics.get("avg_scores"),
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "semantic_report_path": result.updated_state.get("semantic_report_path"),
    }

    summary_path = out / "manifests" / project_uuid / "golden_semantic_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden Semantic Agent test")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    summary = run_golden(args.output)
    return 0 if summary.get("all_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
