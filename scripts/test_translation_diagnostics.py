"""Tests for Developer Translation Diagnostics."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["VM_DEV_MODE"] = "1"

from engines.translation_diagnostics import (
    build_developer_diagnostics,
    dev_diagnostics_enabled,
    export_diagnostics_text,
)


class TestTranslationDiagnostics(unittest.TestCase):
    def test_dev_enabled(self):
        self.assertTrue(dev_diagnostics_enabled())

    def test_build_from_task_info(self):
        info = {
            "task_id": "t1",
            "source_lang": "en",
            "target_lang": "uk",
            "source_segments": ["Hello George Jr."],
            "translation_audits": [
                {
                    "index": 0,
                    "whisper_text": "Hello George Jr.",
                    "raw_translation": "Привет George Jr.",
                    "naturalized_text": "Привет, Джордж-младший.",
                    "final_text": "Привет, Джордж-младший.",
                    "engine": "marian",
                    "quality_score": 88,
                    "duration_ms": 120,
                    "quality_details": {
                        "pipeline_health": {
                            "ok": False,
                            "stages": [
                                {
                                    "stage": "post_mt_restore",
                                    "ok": False,
                                    "issues": ["placeholder_leak"],
                                }
                            ],
                        }
                    },
                }
            ],
        }
        diag = build_developer_diagnostics(info)
        self.assertTrue(diag.get("enabled"))
        self.assertEqual(diag.get("segment_count"), 1)
        self.assertIn("summary", diag)
        segs = diag.get("segments") or []
        self.assertEqual(len(segs), 1)
        stages = segs[0].get("stages") or []
        self.assertTrue(any(s.get("id") == "restore" for s in stages))
        text = export_diagnostics_text(diag)
        self.assertIn("Segment #1", text)
        self.assertIn("Pipeline Status", text)


if __name__ == "__main__":
    unittest.main()
