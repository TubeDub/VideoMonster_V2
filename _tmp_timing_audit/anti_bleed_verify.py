"""Before/after anti-bleed verification using real task audio + fit_segment_audio."""
from __future__ import annotations

import json
import time
from pathlib import Path

from pydub import AudioSegment

from engines.timing_fit import fit_segment_audio

ROOT = Path(r"c:\Users\serhii\Desktop\VideoMonster_V2")
TASK = "66651127645a43958106ccd4d700fb9c"
LOG = ROOT / "debug-ee98a6.log"
OUT = ROOT / "_tmp_timing_audit" / "anti_bleed_verify.json"

studio = json.loads(
    (ROOT / "output" / "studio_sessions" / f"{TASK}.json").read_text(encoding="utf-8")
)
segs = studio.get("segments") or []
# Problematic pair 15->16 (0-based): bleed 4661ms in audit
idx = 15
seg = segs[idx]
nxt = segs[idx + 1]
start = int(seg["start_ms"])
end = int(seg["end_ms"])
next_start = int(nxt["start_ms"])

# Prefer fitted filename; fall back to pause_run / file
session = ROOT / "output" / "sessions" / TASK
candidates = []
for name in (seg.get("fitted_file"), seg.get("file")):
    if not name:
        continue
    candidates.extend(session.rglob(Path(name).name))
# pause fallback by segment id prefix
sid = str(seg.get("segment_id") or "")[:12]
candidates.extend(session.rglob(f"*{sid}*.wav"))
candidates.extend(session.rglob(f"*{sid}*.mp3"))
src = next((p for p in candidates if p.is_file()), None)
if src is None:
    raise SystemExit(f"no audio for seg {idx}: {seg.get('fitted_file')}")

work = ROOT / "_tmp_timing_audit" / "anti_bleed_work"
work.mkdir(parents=True, exist_ok=True)
raw_ms = len(AudioSegment.from_file(str(src)))

# OLD policy: no_speech_trim=True (pre_fitted)
out_old, meta_old = fit_segment_audio(
    src, start, end, next_start=next_start, work_dir=work / "old",
    allow_atempo=False, no_speech_trim=True,
)
old_ms = len(AudioSegment.from_file(out_old))
old_bleed = max(0, (start + old_ms) - next_start)

# NEW policy: no_speech_trim=False
out_new, meta_new = fit_segment_audio(
    src, start, end, next_start=next_start, work_dir=work / "new",
    allow_atempo=False, no_speech_trim=False,
)
new_ms = len(AudioSegment.from_file(out_new))
new_bleed = max(0, (start + new_ms) - next_start)

report = {
    "segment_id": seg.get("segment_id"),
    "index": idx,
    "src": str(src),
    "start_ms": start,
    "end_ms": end,
    "next_start": next_start,
    "slot_ms": end - start,
    "raw_audio_ms": raw_ms,
    "old_policy": {
        "no_speech_trim": True,
        "fitted_ms": old_ms,
        "bleed_ms": old_bleed,
        "strategy": meta_old.get("strategy"),
    },
    "new_policy": {
        "no_speech_trim": False,
        "fitted_ms": new_ms,
        "bleed_ms": new_bleed,
        "strategy": meta_new.get("strategy"),
    },
    "fix_ok": new_bleed == 0 and old_bleed > 0,
}
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

# NDJSON for debug session
payload = {
    "sessionId": "ee98a6",
    "runId": "post-fix",
    "hypothesisId": "H4",
    "location": "anti_bleed_verify.py",
    "message": "before_after_bleed",
    "data": report,
    "timestamp": int(time.time() * 1000),
}
with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps(payload, ensure_ascii=False) + "\n")

print(json.dumps(report, indent=2, ensure_ascii=False))
