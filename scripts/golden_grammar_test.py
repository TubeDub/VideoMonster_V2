#!/usr/bin/env python3
"""Golden dataset runner for Grammar Agent v1.0."""

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

from engines.ai_core.grammar_agent.agent import GrammarAgent

GOLDEN_SEGMENTS = [
    {
        "text": "He said that George Smith went home on 12.05.2024.",
        "timing_text": "Он сказал что George Smith пошёл домой 12.05.2024.",
        "start": 0,
        "end": 3000,
    },
    {
        "text": "Wow!!! That is amazing!",
        "timing_text": "Вау!!! Это удивительно!",
        "start": 3000,
        "end": 6000,
    },
    {
        "text": "I am sad because he left.",
        "timing_text": "Мне грустно потому что он ушёл.",
        "start": 6000,
        "end": 9000,
    },
]


def run_golden(output_dir: Path | None = None) -> dict:
    out = output_dir or (ROOT / "output" / "golden_grammar")
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
            "translated_text": row["timing_text"],
            "semantic_text": row["timing_text"],
            "timing_text": row["timing_text"],
            "start": row["start"],
            "end": row["end"],
        }
        for i, row in enumerate(GOLDEN_SEGMENTS)
    ]

    agent = GrammarAgent(output_dir=out, use_llm=False)
    state = {"segments": segments, "timing_agent_status": "success"}
    result = agent.run(manifest, state, "golden-grammar-run")

    checks = []
    for seg in result.updated_state["segments"]:
        timing = seg.get("timing_text") or ""
        grammar = seg.get("grammar_text") or ""
        ratio = len(grammar) / max(len(timing), 1)
        checks.append(
            {
                "index": seg["index"],
                "timing_text": timing,
                "grammar_text": grammar,
                "length_ratio": round(ratio, 3),
                "ok": bool(grammar.strip()) and 0.85 <= ratio <= 1.15,
            }
        )

    summary = {
        "project_uuid": project_uuid,
        "status": result.status,
        "avg_scores": result.metrics.get("avg_scores"),
        "rule_rewrite_used": result.metrics.get("rule_rewrite_used"),
        "llm_used": result.metrics.get("llm_used"),
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "grammar_report_path": result.updated_state.get("grammar_report_path"),
    }

    summary_path = out / "manifests" / project_uuid / "golden_grammar_summary.json"
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

    parser = argparse.ArgumentParser(description="Golden Grammar Agent test")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    pytest = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_grammar_agent.py", "-q"],
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
