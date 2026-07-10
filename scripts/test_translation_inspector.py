"""Developer Translation Inspector tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["VM_DEV_MODE"] = "1"


def test_integrity_detects_placeholder_damage():
    from engines.translation_inspector import analyze_text_integrity

    good = analyze_text_integrity("Джордж-молодший поїхав додому.")
    bad = analyze_text_integrity("18-річний [#1#] поїхав.")
    assert good.get("ok")
    assert not bad.get("ok")
    assert "placeholder_damaged" in bad.get("issues", [])


def test_build_inspector_from_trace():
    from engines.translation_inspector import build_translation_inspector

    info = {
        "task_id": "test",
        "source_lang": "en",
        "target_lang": "uk",
        "source_segments": ["George Jr. drove home."],
        "translation_audits": [
            {
                "index": 0,
                "whisper_text": "George Jr. drove home.",
                "raw_translation": "Джордж-молодший поїхав додому.",
                "naturalized_text": "Джордж-молодший поїхав додому.",
                "final_text": "Джордж-молодший поїхав додому.",
                "engine": "argos",
                "quality_score": 85.0,
                "quality_details": {
                    "inspector": {
                        "original": "George Jr. drove home.",
                        "preprocessed": "George Jr. drove home.",
                        "entities": ["George Jr."],
                        "entity_map": {"[##1##]": "George Jr."},
                        "masked_text": "[##1##] drove home.",
                        "raw_mt_response": "18-річний [#1#] поїхав.",
                        "after_restore": "Джордж-молодший поїхав.",
                        "after_naturalizer": "Джордж-молодший поїхав додому.",
                        "after_grammar": "Джордж-молодший поїхав додому.",
                        "final": "Джордж-молодший поїхав додому.",
                        "mt_request": {"engine": "argos", "route": "en→uk", "model": ""},
                        "timing_ms": {"mt": 120, "restore": 2, "naturalizer": 30, "total": 152},
                    }
                },
            }
        ],
    }
    report = build_translation_inspector(info)
    assert report.get("enabled")
    seg = report["segments"][0]
    ids = [s["id"] for s in seg["stages"]]
    assert "serialization" in ids
    assert "raw_mt" in ids
    assert "restore" in ids
    raw = next(s for s in seg["stages"] if s["id"] == "raw_mt")
    assert "[#1#]" in raw["text"]
    assert seg["quality"]["engine_score"] == 85.0


def test_export_text():
    from engines.translation_inspector import build_translation_inspector, export_inspector_text

    report = build_translation_inspector({"source_segments": [], "translation_audits": []})
    txt = export_inspector_text(report)
    assert "Developer Translation Inspector" in txt or "Inspector" in txt


def main() -> int:
    test_integrity_detects_placeholder_damage()
    test_build_inspector_from_trace()
    test_export_text()
    print("translation inspector tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
