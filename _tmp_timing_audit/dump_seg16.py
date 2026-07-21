import json
from pathlib import Path

d = json.loads(Path(r"c:\Users\serhii\Desktop\g.json").read_text(encoding="utf-8"))
out = Path(r"c:\Users\serhii\Desktop\VideoMonster_V2\_tmp_timing_audit\seg16.json")
keys = [
    "segment_id",
    "adaptation_executed",
    "adaptation_status",
    "adaptation_skip_reason",
    "adaptation_decision",
    "decision_trace",
    "overlap_info",
    "start_time_ms",
    "end_time_ms",
    "slot_ms",
    "final_tts_duration_ms",
    "actual_duration_ms",
    "original_duration_ms",
    "first_tts_duration_ms",
    "gap_absorb",
    "block_merge",
    "merge_info",
    "adaptation_stages",
    "algorithm_reason",
    "speech_difference_ms",
    "tts_speech_end_ms",
    "original_speech_end_ms",
    "path_chain",
    "warnings",
]
s = d["segments"][15]
out.write_text(
    json.dumps({k: s.get(k) for k in keys}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
# also studio session if present
sid = d.get("task_id")
studio = Path(r"c:\Users\serhii\Desktop\VideoMonster_V2\output\studio_sessions") / f"{sid}.json"
print("wrote", out)
print("studio_exists", studio.exists())
if studio.exists():
    st = json.loads(studio.read_text(encoding="utf-8"))
    segs = st.get("segments") or []
    if len(segs) > 15:
        ss = segs[15]
        Path(r"c:\Users\serhii\Desktop\VideoMonster_V2\_tmp_timing_audit\seg16_studio.json").write_text(
            json.dumps(
                {
                    k: ss.get(k)
                    for k in [
                        "segment_id",
                        "start_ms",
                        "end_ms",
                        "tts_ms",
                        "fitted_ms",
                        "fitted_file",
                        "file",
                        "overflow_ms",
                        "slot_overflow",
                        "video_adapt_mode",
                        "adaptation_executed",
                        "adaptation_status",
                        "adaptation_decision",
                        "overflow_decision",
                        "playback_duration",
                        "actual_duration_ms",
                        "slot_ms",
                        "merge_adjusted_start",
                        "place_start",
                        "container_status",
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("wrote studio seg16")
