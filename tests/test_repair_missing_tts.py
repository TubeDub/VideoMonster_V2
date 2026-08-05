# -*- coding: utf-8 -*-
"""Stage 22: repair force-split children missing TTS before handoff."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_repair_missing_tts_files_fills_gap(tmp_path, monkeypatch):
    from api import auto_dub_api as api

    monkeypatch.setattr(api, "_artifacts_dir", lambda *_a, **_k: tmp_path)
    segs = [
        {
            "index": 0,
            "segment_id": "aaa",
            "text": "Привіт світе.",
            "final_tts_text": "Привіт світе.",
            "status": "pending_regen",
            "tts_status": "pending_regen",
            "needs_re_tts": True,
        }
    ]

    def _fake_regen(*_a, **_k):
        return ("repaired.mp3", 1200)

    with patch.object(api, "_regen_segment_tts", side_effect=_fake_regen):
        with patch.object(api, "_commit_tts_group_result", return_value=None):
            stats = api._repair_missing_tts_files(
                segs,
                voice="mykyta",
                task_info={"tts_engine": "tts_uk"},
                task_id="t1",
            )
    assert stats["repaired"] == 1
    assert segs[0]["file"] == "repaired.mp3"
    assert segs[0]["tts_file_path"] == "repaired.mp3"
    assert segs[0]["needs_re_tts"] is False
    assert segs[0]["status"] == "generated"
