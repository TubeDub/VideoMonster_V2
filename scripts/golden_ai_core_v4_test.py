#!/usr/bin/env python3
"""Golden Dataset — AI Core 4.0 architecture regression (semantic + peer validation)."""

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

from engines.ai_core.architecture_validation import write_architecture_validation, ArchitectureMetrics
from engines.ai_core.peer_validation import validate_upstream_batch
from engines.ai_core.semantic_agent.agent import SemanticAgent

GOLDEN = [
    {
        "text": "George Jr. would receive an acceptance letter from USC's film school.",
        "translated_text": "Джордж молодший отримає листа від компанії з фільму \"Скарб США.\"",
    },
    {
        "text": "But, as he was driving, George Jr. could not help but feel dread.",
        "translated_text": "Але коли Джордж ехав за кермом, він не мог не відчувати страх.",
    },
]


def run(output_dir: Path | None = None) -> dict:
    out = output_dir or (ROOT / "output" / "golden_ai_core_v4")
    out.mkdir(parents=True, exist_ok=True)
    task_id = f"golden-v4-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()

    manifest = {"project_uuid": str(uuid.uuid4()), "source_lang": "en", "target_lang": "uk"}
    segments = [
        {"index": i, "text": r["text"], "translated_text": r["translated_text"]}
        for i, r in enumerate(GOLDEN)
    ]

    peer_in = validate_upstream_batch("semantic", segments, target_lang="uk")
    agent = SemanticAgent(output_dir=out)
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(
            manifest,
            {"segments": segments, "translation_agent_status": "success"},
            task_id,
        )

    texts = [str(s.get("semantic_text") or "") for s in result.updated_state["segments"]]
    improved = sum(
        1
        for t, ref in zip(texts, GOLDEN)
        if "Скарб США" not in t
        and ("USC" in t or "Джордж-молодший" in t or "не мог" not in t)
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    metrics = ArchitectureMetrics(task_id=task_id)
    metrics.record_agent("semantic", execution_time_ms=elapsed_ms, peer_ok=peer_in.ok)
    metrics.pipeline_status = "completed" if improved >= len(GOLDEN) // 2 else "partial"
    write_architecture_validation(task_id, metrics, app_dir=ROOT)

    summary = {
        "engine": "AI Core 4.0",
        "improved_count": improved,
        "total": len(GOLDEN),
        "passed": improved >= len(GOLDEN) // 2 and peer_in.ok,
        "peer_validation_ok": peer_in.ok,
        "elapsed_ms": round(elapsed_ms, 1),
        "outputs": texts,
    }
    path = out / f"golden_ai_core_v4_{task_id}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()
