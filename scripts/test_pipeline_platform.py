#!/usr/bin/env python3
"""Smoke test for Pipeline Platform (mandatory TZ)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))


def main() -> int:
    from engines.pipeline_platform import bootstrap_stages, list_stages, build_dev_pipeline_view

    bootstrap_stages()
    stages = list_stages()
    print(f"stages: {len(stages)}")
    info = {
        "segments_data": [{"index": 0, "text": "Hello", "source_text": "Hello"}],
        "translation_audits": [{"index": 0, "source_text": "Hello", "final_text": "Привіт"}],
        "source_lang": "en",
        "target_lang": "uk",
    }
    view = build_dev_pipeline_view(info)
    print(json.dumps({"segment_count": view["segment_count"], "labels": [c["label"] for c in view["segments"][0]["chain"]]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
