#!/usr/bin/env python3
"""Smoke tests for Dub Studio module."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    print("Dub Studio tests")
    from engines.dub_studio.config import dub_studio_enabled
    from engines.dub_studio.emotion import emotion_to_tts_params, extract_emotion
    from engines.dub_studio.fx.chain import FxChain, FxSlotSpec
    from engines.dub_studio.fx.registry import FX_REGISTRY, list_plugins
    from engines.dub_studio.models import StudioSegment
    from engines.dub_studio.timing import (
        apply_hard_anchor,
        container_status,
        update_segment_timing,
    )

    assert not dub_studio_enabled()
    print("  OK disabled by default")

    assert len(FX_REGISTRY) >= 6
    assert any(p["plugin_id"] == "compressor" for p in list_plugins())
    print("  OK fx plugins")

    seg = StudioSegment("s1", 0, "test", 1000, 4000)
    apply_hard_anchor(seg)
    assert seg.hard_anchor_ms == 1000
    update_segment_timing(seg, tts_ms=5000)
    assert seg.container_status == container_status(125.0)
    print("  OK timing anchor + container status")

    params = emotion_to_tts_params("HAPPY")
    assert params.get("rate")
    print("  OK emotion bridge")

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        os.environ["VM_DUB_STUDIO_ENABLED"] = "1"
        try:
            from engines.dub_studio.service import DubStudioService

            svc = DubStudioService(base)
            project = svc.create_project(title="Test")
            assert project.project_id
            print("  OK create project")

            review = {
                "segments": [
                    {
                        "index": 0,
                        "start_ms": 0,
                        "end_ms": 3000,
                        "final_text": "Привіт світ",
                        "duration_ms": 2800,
                    }
                ]
            }
            review_path = base / "test_review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            (base / "output").mkdir(exist_ok=True)
            shutil.copy2(review_path, base / "output" / "test_review.json")
            imported = svc.import_review(base / "output" / "test_review.json", title="Import test")
            assert len(imported.segments) == 1
            assert imported.segments[0].emotion
            print("  OK import review")

            chain = FxChain([FxSlotSpec("passthrough")])
            work = base / "fx"
            work.mkdir()
            inp = work / "in.wav"
            from pydub import AudioSegment

            AudioSegment.silent(200).export(inp, format="wav")
            result = chain.process_sync(inp, work)
            assert Path(result.output_path).is_file()
            print("  OK fx chain")
        finally:
            os.environ.pop("VM_DUB_STUDIO_ENABLED", None)

    print("\nAll Dub Studio tests passed.")


if __name__ == "__main__":
    main()
