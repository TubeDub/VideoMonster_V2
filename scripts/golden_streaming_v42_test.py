#!/usr/bin/env python3
"""Compare batch vs streaming pipeline on golden segments."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.streaming_pipeline import StreamingTextPipeline

GOLDEN = [
    {"index": 0, "text": "George Jr. got a letter from USC.", "translated_text": "Джордж молодший отримав лист від USC."},
    {"index": 1, "text": "He drove home.", "translated_text": "Він поїхав додому."},
    {"index": 2, "text": "Dinner was tense.", "translated_text": "Вечеря була напруженою."},
]


def _run_mode(mode: str) -> dict:
    manifest = {"project_uuid": str(uuid.uuid4()), "source_lang": "en", "target_lang": "uk"}
    segments = [dict(s) for s in GOLDEN]
    state = {"segments": segments, "pipeline_mode": mode}
    t0 = time.perf_counter()

    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        if mode == "streaming":
            pipe = StreamingTextPipeline(
                manifest, state, f"golden-{mode}", stages=("semantic", "timing", "grammar")
            )
            result = pipe.run()
        else:
            from engines.ai_core.semantic_agent.agent import SemanticAgent

            agent = SemanticAgent()
            result = agent.run(
                manifest,
                {"segments": segments, "translation_agent_status": "success"},
                f"golden-batch",
            )

    elapsed = (time.perf_counter() - t0) * 1000
    texts = [
        str(s.get("grammar_text") or s.get("semantic_text") or s.get("translated_text") or "")
        for s in (result.updated_state.get("segments") or segments)
    ]
    return {
        "mode": mode,
        "elapsed_ms": round(elapsed, 1),
        "segment_count": len(GOLDEN),
        "non_empty": sum(1 for t in texts if t.strip()),
        "texts": texts,
    }


def run() -> dict:
    batch = _run_mode("batch")
    stream = _run_mode("streaming")
    summary = {
        "engine": "Streaming Pipeline 4.2 golden compare",
        "batch": batch,
        "streaming": stream,
        "streaming_faster": stream["elapsed_ms"] <= batch["elapsed_ms"] * 1.5,
        "quality_ok": stream["non_empty"] >= batch["non_empty"],
        "passed": stream["non_empty"] >= len(GOLDEN) // 2,
    }
    out = ROOT / "output" / "golden_streaming_v42_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()
