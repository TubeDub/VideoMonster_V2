#!/usr/bin/env python3
"""Golden dataset — Translation + Semantic Engine v4.0 regression."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.semantic_agent.agent import SemanticAgent

GOLDEN = [
    {
        "text": "An 18-year-old boy named George Jr. drove through his hometown on his way home for dinner.",
        "translated_text": "18-річний Джордж молодший поїхав додому на вечерю через рідне місто.",
        "legacy_bad": "18-річний Джордж молодший поїхав додому на вечерю через рідне місто.",
    },
    {
        "text": "But, as he was driving, George Jr. could not help but feel like he was really dreading actually getting there.",
        "translated_text": "Але коли Джордж ехав за кермом, він не мог не відчувати, що справді боявся там брати.",
        "legacy_bad": "Але коли Джордж ехав за кермом, він не мог не відчувати, що справді боявся там брати.",
    },
    {
        "text": "he said, George, I know people at USC. Let me make some calls.",
        "translated_text": "Джордж, я знаю людей в Університеті, дозвольте мені зробити кілька дзвінків.",
        "legacy_bad": "Джордж, я знаю людей в Університеті, дозвольте мені зробити кілька дзвінків.",
    },
    {
        "text": "George Jr. would receive an acceptance letter from USC's film school.",
        "translated_text": "Джордж молодший отримає листа від компанії з фільму \"Скарб США.\"",
        "legacy_bad": "Джордж молодший отримає листа від компанії з фільму \"Скарб США.\"",
    },
]


def run(output_dir: Path | None = None) -> dict:
    out = output_dir or (ROOT / "output" / "golden_semantic_v4")
    out.mkdir(parents=True, exist_ok=True)
    project_uuid = str(uuid.uuid4())
    manifest = {
        "project_uuid": project_uuid,
        "planner_version": "3.0",
        "source_lang": "en",
        "target_lang": "uk",
    }
    segments = [
        {"index": i, "text": r["text"], "translated_text": r["translated_text"]}
        for i, r in enumerate(GOLDEN)
    ]
    agent = SemanticAgent(output_dir=out)
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(manifest, {"segments": segments, "translation_agent_status": "success"}, "golden-v4-run")

    comparisons = []
    improved = 0
    for seg, ref in zip(result.updated_state["segments"], GOLDEN):
        new = str(seg.get("semantic_text") or "")
        old = ref["legacy_bad"]
        better = (
            ("Скарб США" in old and "Скарб США" not in new)
            or ("USC" in new and "USC" not in old)
            or ("Джордж-молодший" in new and "Джордж молодший" in old)
            or ("не мог" in old and "не мог" not in new)
            or ("ехав" in old and "ехав" not in new and "їхав" in new)
            or (new.strip() != old.strip() and "Університет" not in new and "USC" in new)
        )
        if better:
            improved += 1
        comparisons.append({"index": seg["index"], "old": old, "new": new, "improved": better})

    summary = {
        "engine": "Semantic v4.0",
        "improved_count": improved,
        "total": len(GOLDEN),
        "passed": improved >= len(GOLDEN) // 2,
        "comparisons": comparisons,
    }
    path = out / "manifests" / project_uuid / "golden_semantic_v4_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()
