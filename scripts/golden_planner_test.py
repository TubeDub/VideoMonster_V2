"""Golden planner regression — compare manifest keys against contract."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.golden_dataset import compare_manifest_keys
from engines.ai_core.planner_agent import PlannerAgent


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden planner manifest test")
    parser.add_argument(
        "video_path",
        nargs="?",
        default=os.getenv("VM_GOLDEN_VIDEO", ""),
        help="Optional test video path",
    )
    parser.add_argument("--target-lang", default="uk")
    parser.add_argument("--source-lang", default="en")
    args = parser.parse_args()

    if not args.video_path or not Path(args.video_path).is_file():
        print("SKIP: no test video (set path arg or VM_GOLDEN_VIDEO)")
        return 0

    agent = PlannerAgent()
    result = agent.run(
        args.video_path,
        args.target_lang,
        source_lang=args.source_lang,
        task_id="golden_planner",
    )
    manifest_path = Path(result.updated_state["manifest_path"])
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    cmp = compare_manifest_keys(manifest)
    print(json.dumps({"status": result.status, "compare": cmp}, indent=2))
    return 0 if cmp["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
