"""E2E-style test: Final text must reach TTS unchanged after review."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_final_texts_from_audits():
    from engines.tts_text_path import build_tts_trace_rows, final_texts_from_info, find_mismatches

    info = {
        "source_segments": ["Hello world"],
        "translation_audits": [
            {
                "index": 0,
                "whisper_text": "Hello world",
                "raw_translation": "Привет мир raw",
                "naturalized_text": "Привет, мир!",
                "final_text": "Привіт, світ!",
                "tts_text": "Привіт, світ!",
            }
        ],
        "segments_data": [{"index": 0, "text": "Привіт, світ!", "file": None}],
    }
    finals = final_texts_from_info(info)
    assert finals == ["Привіт, світ!"]
    rows = build_tts_trace_rows(info, finals)
    assert find_mismatches(rows) == []


def test_semantic_skip_when_adapt_false():
    from engines.semantic_adaptation import prepare_tts_groups_semantic

    groups = [{"indices": [0], "text": "Привіт, світ!", "timing": [0, 3000]}]
    out, log = prepare_tts_groups_semantic(
        groups,
        source_segments=["Hello"],
        src_lang="en",
        tgt_lang="uk",
        task_id="t",
        app_dir=ROOT,
        adapt_text=False,
    )
    assert out[0]["text"] == "Привіт, світ!"
    assert len(log._records) == 0


def test_mismatch_detected():
    from engines.tts_text_path import build_tts_trace_rows, find_mismatches

    info = {
        "source_segments": ["Hi"],
        "translation_audits": [
            {
                "index": 0,
                "final_text": "Привіт!",
                "naturalized_text": "Привіт коротко",
                "raw_translation": "raw",
            }
        ],
        "segments_data": [{"text": "Привіт!"}],
    }
    rows = build_tts_trace_rows(info, ["Привіт коротко"])
    mm = find_mismatches(rows)
    assert len(mm) == 1
    assert mm[0]["final"] == "Привіт!"
    assert mm[0]["tts_input"] == "Привіт коротко"


def main() -> int:
    test_final_texts_from_audits()
    test_semantic_skip_when_adapt_false()
    test_mismatch_detected()
    print("tts text path tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
