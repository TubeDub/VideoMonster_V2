"""Focused tests for hardened import / translation infra APIs."""

from __future__ import annotations

import json
from pathlib import Path

from api import import_api
from api.translation_api import _safe_id, _summarize_report


def test_find_import_file_ignores_meta(tmp_path, monkeypatch):
    imports = tmp_path / "imports"
    imports.mkdir()
    monkeypatch.setattr(import_api, "IMPORT_DIR", imports)

    iid = "abcd1234ef00"
    media = imports / f"{iid}.mp4"
    meta = imports / f"{iid}.meta.json"
    media.write_bytes(b"video")
    meta.write_text(json.dumps({"original": "clip.mp4"}), encoding="utf-8")

    # Create meta first so glob order would prefer it without the fix.
    found = import_api._find_import_file(iid)
    assert found is not None
    assert found.name == media.name


def test_safe_import_id_rejects_traversal():
    assert import_api._safe_import_id("../etc/passwd") is None
    assert import_api._safe_import_id("abcd1234ef00") == "abcd1234ef00"


def test_detect_webm_is_video_not_audio():
    target = import_api.detect_import_target("clip.webm")
    assert target["kind"] == "video"
    assert target["route"] == "/dub"


def test_translation_safe_id_reserves_reports():
    assert _safe_id("reports") is None
    assert _safe_id("project") is None
    assert _safe_id("task_01") == "task_01"


def test_translation_summary_counts_segments():
    summary = _summarize_report(
        {
            "source_lang": "en",
            "target_lang": "ru",
            "segments": [{"text": "a"}, {"text": "b"}],
        }
    )
    assert summary["segment_count"] == 2
    assert summary["source_lang"] == "en"
