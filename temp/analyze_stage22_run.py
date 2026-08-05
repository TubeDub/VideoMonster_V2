# -*- coding: utf-8 -*-
import json
import re
from collections import Counter
from pathlib import Path

data = json.loads(Path(r"c:\Users\serhii\Desktop\1.json").read_text(encoding="utf-8"))
segs = data.get("segments") or []

print("task_id", data.get("task_id"))
print("n_segments", len(segs))
print("summary", data.get("summary"))
print("flags", data.get("flags"))

garbage_re = re.compile(r"Саме про|тут ідеться", re.I)
garbage = []
for s in segs:
    t = str(s.get("final_tts_text") or s.get("translated_text") or "")
    if garbage_re.search(t):
        garbage.append((s.get("index"), t[:120]))

fills = []
statuses = Counter()
expand_n = 0
under_abs = 0
has22 = 0
has21 = 0
soft_pads = 0
voice = Counter()
for s in segs:
    s21 = s.get("stage21") or {}
    s22 = s.get("stage22") or {}
    if s22:
        has22 += 1
    if s21:
        has21 += 1
    fr = float(s.get("fill_ratio") or s22.get("fill_ratio") or s21.get("fill_ratio") or 0)
    fills.append(fr)
    st = s22.get("final_status") or s21.get("final_status") or ""
    statuses[st] += 1
    if s.get("expand_executed") or s21.get("expand_executed") or s22.get("expand_executed"):
        expand_n += 1
    delta = int(s21.get("delta") or s22.get("delta") or 0)
    if delta < -350:
        under_abs += 1
    soft_pads += int(s.get("soft_pad_count") or s21.get("soft_pad_count") or 0)
    voice[str(s.get("voice") or "")] += 1

in_band = sum(1 for f in fills if 0.90 <= f <= 1.12)
print("garbage_hits", len(garbage), garbage[:5])
print("fill_in_band", in_band, "/", len(fills), f"({100*in_band/max(1,len(fills)):.1f}%)")
print("fill min/avg/max", round(min(fills), 3), round(sum(fills)/len(fills), 3), round(max(fills), 3))
print("status", dict(statuses))
print("expand_executed", expand_n)
print("delta<-350", under_abs)
print("has stage21/22", has21, has22)
print("soft_pad total", soft_pads)
print("voices", dict(voice))

place = [o for o in (data.get("overlaps") or []) if o.get("type") == "audio_placement_overlap"]
ms = sorted(int(o.get("overlap_ms") or 0) for o in place)
print("placement_overlaps", len(place), "ms", ms)
print("placement>400", sum(1 for m in ms if m > 400))
print("segment_overflow", sum(1 for o in (data.get("overlaps") or []) if o.get("type") == "segment_overflow"))

# timing score search
scores = []
for s in segs:
    for path in (
        s.get("timing_score"),
        (s.get("stage21") or {}).get("timing_score"),
        (s.get("decision_trace") or {}).get("timing_score"),
    ):
        if path is not None:
            try:
                scores.append(float(path))
            except Exception:
                pass
print("timing_scores_n", len(scores), "avg", round(sum(scores)/len(scores), 2) if scores else None)

# dead_air with fill>=0.90
weird = []
for s in segs:
    s21 = s.get("stage21") or {}
    fr = float(s.get("fill_ratio") or 0)
    st = s21.get("final_status")
    if st == "dead_air_risk" and fr >= 0.90:
        weird.append((s.get("index"), fr, s21.get("delta"), s.get("expand_executed")))
print("dead_air but fill>=0.90", weird)

# out of band
oob = [(s.get("index"), s.get("fill_ratio"), (s.get("stage21") or {}).get("final_status")) for s in segs if not (0.90 <= float(s.get("fill_ratio") or 0) <= 1.12)]
print("out_of_band", oob)

print("tts_pipeline", json.dumps(data.get("tts_pipeline") or {}, ensure_ascii=False)[:1000])

# per seg compact
print("--- per seg ---")
for s in segs:
    s21 = s.get("stage21") or {}
    t = (s.get("final_tts_text") or "")[:60].replace("\n", " ")
    print(
        f"#{s.get('index'):02d} fill={float(s.get('fill_ratio') or 0):.3f} "
        f"st={s21.get('final_status')} exp={s.get('expand_executed')} "
        f"d={s21.get('delta')} soft={s.get('soft_pad_count')} "
        f"split={s.get('force_split_executed') or s21.get('force_split_executed')} "
        f"text={t!r}"
    )
