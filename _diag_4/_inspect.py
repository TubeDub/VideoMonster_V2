import json
from pathlib import Path

bad = "e6af9583f9bb4a7eb1cdd4f1aa74f0bd"
root = Path(__file__).resolve().parent
after = json.loads((root / "snapshot_after.json").read_text(encoding="utf-8"))
before = json.loads((root / "snapshot_before.json").read_text(encoding="utf-8"))
print("before n", len(before), "after n", len(after))
for label, segs in [("BEFORE", before), ("AFTER", after)]:
    print("====", label)
    missing = 0
    for i, s in enumerate(segs):
        f = s.get("file") or s.get("tts_file_path") or ""
        if not f:
            missing += 1
        sid = str(s.get("segment_id") or "")
        meta = s.get("stage19c") or {}
        print(
            f"{i:02d} {sid[:16]} file={bool(f)} tts={s.get('tts_ms') or s.get('playback_duration')} "
            f"split={s.get('stage19c_split_done')} parent={meta.get('split_parent_idx')} "
            f"children={meta.get('split_children')} status={s.get('status')} "
            f"algo={(s.get('algorithm_reason') or '')[:28]} "
            f"text={(s.get('plain_text') or s.get('text') or '')[:45]!r}"
        )
        if bad in sid:
            print("  FILE", repr(s.get("file")), repr(s.get("tts_file_path")))
            print("  stage19c", meta)
            print("  split_from", s.get("split_from_segment_id"), "reissued", s.get("reissued_from"))
    print("missing_files", missing)
