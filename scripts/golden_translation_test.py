#!/usr/bin/env python3
"""Golden dataset runner for Translation Agent entity preservation."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.translation_agent.agent import TranslationAgent
from engines.ai_core.translation_agent.validators.entity_validator import validate_entities

GOLDEN_SEGMENTS = [
    {
        "text": "John Smith visited Berlin on 12.05.2024 with 100 guests.",
        "expect_entities": ["John Smith", "12.05.2024", "100"],
    },
    {
        "text": "NASA launched Artemis at 08:30 with 42% fuel reserve.",
        "expect_entities": ["NASA", "Artemis", "42"],
    },
    {
        "text": "Dr. Elena Petrova signed contract #9912.",
        "expect_entities": ["Elena", "9912"],
    },
]


def _mock_translate(text: str, source: str, target: str) -> str:
    """Deterministic mock preserving entities for offline golden run."""
    return text


def run_golden(output_dir: Path | None = None) -> dict:
    out = output_dir or (ROOT / "output" / "golden_translation")
    out.mkdir(parents=True, exist_ok=True)

    project_uuid = str(uuid.uuid4())
    manifest = {
        "project_uuid": project_uuid,
        "planner_version": "3.0",
        "source_lang": "en",
        "target_lang": "uk",
        "capability_matrix": {"llm": False},
        "success_criteria": {"translate": {"segments_min": 1}},
    }

    segments = [
        {"index": i, "text": row["text"], "start": i * 2000, "end": (i + 1) * 2000}
        for i, row in enumerate(GOLDEN_SEGMENTS)
    ]

    agent = TranslationAgent(output_dir=out)

    from engines.ai_core.translation_agent.retry_policy import TranslateAttemptResult

    def _patched(text, source, target, registry, **kwargs):
        translated = _mock_translate(text, source, target)
        return TranslateAttemptResult(
            translated=translated,
            translator_name="golden_mock",
            success=True,
            attempt=1,
            confidence=0.95,
        )

    from unittest.mock import patch

    with patch(
        "engines.ai_core.translation_agent.agent.translate_with_fallback",
        side_effect=_patched,
    ):
        result = agent.run(manifest, {"segments": segments}, "golden-run")

    entity_results = []
    passed = 0
    for i, row in enumerate(GOLDEN_SEGMENTS):
        seg = result.updated_state["segments"][i]
        ev = validate_entities(row["text"], seg.get("translated_text") or "")
        ok = ev.ok
        if ok:
            passed += 1
        entity_results.append(
            {
                "index": i,
                "source": row["text"],
                "translated": seg.get("translated_text"),
                "entity_ok": ok,
                "confidence": ev.confidence,
                "missing": ev.missing,
            }
        )

    summary = {
        "project_uuid": project_uuid,
        "total": len(GOLDEN_SEGMENTS),
        "entity_passed": passed,
        "entity_failed": len(GOLDEN_SEGMENTS) - passed,
        "agent_status": result.status,
        "segments": entity_results,
    }

    report_path = out / "manifests" / project_uuid / "golden_translation_summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved: {report_path}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden translation entity test")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    summary = run_golden(args.output)
    return 0 if summary["entity_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
