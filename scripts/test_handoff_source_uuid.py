"""Regression: studio handoff must not fail on shared source_segment_uuid.

Reproduces diagnostic 33.zip / task db4a484c… — HandoffViolation caused by two
dub segments that legitimately share one STT ``source_segment_uuid`` after a
split/merge. WAV/TTS integrity was healthy; only the uniqueness rule was wrong.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.pipeline_integrity.runtime_validator import validate_runtime  # noqa: E402
from engines.pipeline_integrity.uuid_chain import (  # noqa: E402
    UNIQUE_UUID_FIELDS,
    assert_uuids_unique,
)


def test_source_uuid_not_in_unique_fields():
    assert "source_segment_uuid" not in UNIQUE_UUID_FIELDS
    assert "segment_uuid" in UNIQUE_UUID_FIELDS
    print("OK test_source_uuid_not_in_unique_fields")


def test_handoff_allows_shared_source_uuid():
    snap = ROOT / "_tmp_33_inspect" / "snapshot_after.json"
    if not snap.is_file():
        # Fallback synthetic case matching the real failure shape.
        segs = [
            {
                "segment_uuid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "source_segment_uuid": "sharedsharedsharedsharedsharedsh",
                "translation_uuid": "t1t1t1t1t1t1t1t1t1t1t1t1t1t1t1t1",
                "adaptation_uuid": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
                "tts_uuid": "u1u1u1u1u1u1u1u1u1u1u1u1u1u1u1u1",
                "audio_uuid": "b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1",
                "merge_uuid": "m1m1m1m1m1m1m1m1m1m1m1m1m1m1m1m1",
                "start_ms": 0,
                "end_ms": 1000,
                "tts_status": "generated",
                "status": "generated",
                "tts_file_path": __file__,  # any existing file
                "file": __file__,
            },
            {
                "segment_uuid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "source_segment_uuid": "sharedsharedsharedsharedsharedsh",
                "translation_uuid": "t2t2t2t2t2t2t2t2t2t2t2t2t2t2t2t2",
                "adaptation_uuid": "a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2",
                "tts_uuid": "u2u2u2u2u2u2u2u2u2u2u2u2u2u2u2u2",
                "audio_uuid": "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2",
                "merge_uuid": "m2m2m2m2m2m2m2m2m2m2m2m2m2m2m2m2",
                "start_ms": 1000,
                "end_ms": 2000,
                "tts_status": "generated",
                "status": "generated",
                "tts_file_path": __file__,
                "file": __file__,
            },
        ]
        info = {"segments_data": segs}

        def resolve(ref, task_info=None):
            return Path(str(ref))

        result = validate_runtime(
            info,
            stage="studio_handoff",
            require_tts=True,
            require_contracts=False,
            resolve_audio=resolve,
            attempt_recovery=False,
        )
        assert result.ok, result.errors
        assert_uuids_unique(segs)
        print("OK test_handoff_allows_shared_source_uuid (synthetic)")
        return

    segs = json.loads(snap.read_text(encoding="utf-8"))
    info = {"segments_data": segs}

    def resolve(ref, task_info=None):
        p = Path(str(ref))
        return p if p.is_absolute() else ROOT / p

    result = validate_runtime(
        info,
        stage="studio_handoff",
        require_tts=True,
        require_contracts=False,
        resolve_audio=resolve,
        attempt_recovery=True,
    )
    assert result.ok, f"handoff still blocked: {result.errors} checks={result.checks}"
    assert_uuids_unique(segs)
    print("OK test_handoff_allows_shared_source_uuid (snapshot_after)")


def main() -> int:
    tests = [
        test_source_uuid_not_in_unique_fields,
        test_handoff_allows_shared_source_uuid,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
