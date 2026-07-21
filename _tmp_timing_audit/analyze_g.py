"""Analyze g.json for Root Cause Audit — timing integrity after adaptation."""
from __future__ import annotations

import json
from pathlib import Path

SRC = Path(r"c:\Users\serhii\Desktop\g.json")
OUT = Path(r"c:\Users\serhii\Desktop\VideoMonster_V2\_tmp_timing_audit")
OUT.mkdir(parents=True, exist_ok=True)

d = json.loads(SRC.read_text(encoding="utf-8"))
segs = d.get("segments") or []

rows = []
for i, s in enumerate(segs):
    ov = s.get("overlap_info") or {}
    gap = s.get("gap_absorb") or {}
    merge = s.get("merge_info") or {}
    block = s.get("block_merge") or {}
    ad = s.get("adaptation_decision") or {}
    dt = s.get("decision_trace") or {}
    row = {
        "index": i,
        "segment_id": s.get("segment_id"),
        "adaptation_executed": s.get("adaptation_executed"),
        "adaptation_status": s.get("adaptation_status"),
        "adaptation_skip_reason": s.get("adaptation_skip_reason"),
        "overflow_ms_overlap": ov.get("overflow_ms"),
        "overflow_pct": ov.get("overflow_pct"),
        "slot_overflow": ov.get("slot_overflow"),
        "start_time_ms": s.get("start_time_ms"),
        "end_time_ms": s.get("end_time_ms"),
        "slot_ms": s.get("slot_ms"),
        "original_duration_ms": s.get("original_duration_ms"),
        "first_tts_duration_ms": s.get("first_tts_duration_ms"),
        "final_tts_duration_ms": s.get("final_tts_duration_ms"),
        "actual_duration_ms": s.get("actual_duration_ms"),
        "predicted_duration_ms": s.get("predicted_duration_ms"),
        "estimated_speech_duration_ms": s.get("estimated_speech_duration_ms"),
        "speech_difference_ms": s.get("speech_difference_ms"),
        "original_speech_end_ms": s.get("original_speech_end_ms"),
        "tts_speech_end_ms": s.get("tts_speech_end_ms"),
        "video_adapt_mode": gap.get("mode"),
        "video_stretch_ratio": gap.get("video_stretch_ratio"),
        "merge_adjusted_start": block.get("merge_adjusted_start") or merge.get("merge_adjusted_start"),
        "merge_adjusted_slot_ms": block.get("merge_adjusted_slot_ms"),
        "block_merged_with_next": block.get("block_merged_with_next") or merge.get("block_merged_with_next"),
        "merged_into": merge.get("merged_into"),
        "adaptation_decision_overflow": ad.get("overflow_ms"),
        "adaptation_decision_underflow": ad.get("underflow_ms"),
        "decision": ad.get("decision"),
        "decision_trace_summary": (dt.get("summary") if isinstance(dt, dict) else None),
        "algorithm_reason": s.get("algorithm_reason"),
        "warnings": s.get("warnings") or [],
        "adaptation_stages": s.get("adaptation_stages") or [],
    }
    # derived mismatches
    start = int(row["start_time_ms"] or 0)
    end = int(row["end_time_ms"] or 0)
    slot = int(row["slot_ms"] or max(0, end - start))
    final_tts = int(row["final_tts_duration_ms"] or row["actual_duration_ms"] or 0)
    first_tts = int(row["first_tts_duration_ms"] or 0)
    orig = int(row["original_duration_ms"] or 0)
    row["derived_slot_ms"] = end - start if end > start else slot
    row["tts_vs_slot_overflow"] = max(0, final_tts - slot) if final_tts and slot else None
    row["uses_original_as_slot_suspect"] = bool(orig and final_tts and orig != final_tts and slot == orig)
    row["speech_end_drift_ms"] = None
    if row["original_speech_end_ms"] is not None and row["tts_speech_end_ms"] is not None:
        row["speech_end_drift_ms"] = int(row["tts_speech_end_ms"]) - int(row["original_speech_end_ms"])
    rows.append(row)

# overlaps between consecutive segments by start/end
timeline_issues = []
for a, b in zip(rows, rows[1:]):
    if a.get("merged_into") is not None or b.get("merged_into") is not None:
        continue
    ae = int(a["end_time_ms"] or 0)
    bs = int(b["start_time_ms"] or 0)
    a_tts_end = int(a["start_time_ms"] or 0) + int(a["final_tts_duration_ms"] or 0)
    if ae > bs:
        timeline_issues.append(
            {
                "type": "slot_overlap",
                "a": a["index"] + 1,
                "b": b["index"] + 1,
                "overlap_ms": ae - bs,
            }
        )
    if a_tts_end > bs + 40:
        timeline_issues.append(
            {
                "type": "audio_bleeds_into_next_slot",
                "a": a["index"] + 1,
                "b": b["index"] + 1,
                "bleed_ms": a_tts_end - bs,
                "a_tts_end": a_tts_end,
                "b_start": bs,
                "a_slot_end": ae,
                "a_final_tts": a["final_tts_duration_ms"],
                "a_slot_ms": a["slot_ms"],
                "a_overflow_reported": a["overflow_ms_overlap"],
                "a_adaptation_status": a["adaptation_status"],
            }
        )

# classify overflow==0 meaning
overflow_zero_analysis = []
for r in rows:
    ov = int(r["overflow_ms_overlap"] or 0)
    if not r["adaptation_executed"]:
        continue
    derived = r["tts_vs_slot_overflow"]
    overflow_zero_analysis.append(
        {
            "index": r["index"] + 1,
            "reported_overflow_ms": ov,
            "derived_tts_minus_slot": derived,
            "final_tts": r["final_tts_duration_ms"],
            "slot_ms": r["slot_ms"],
            "first_tts": r["first_tts_duration_ms"],
            "video_adapt_mode": r["video_adapt_mode"],
            "interpretation": (
                "TRUE_FIT"
                if derived == 0
                else "REPORTED_ZERO_BUT_TTS_STILL_OVERFLOWS_SLOT"
                if derived and derived > 0 and ov == 0
                else "STRETCHED_OR_FITTED_METADATA"
                if r["video_adapt_mode"]
                else "UNKNOWN"
            ),
        }
    )

report = {
    "task_id": d.get("task_id"),
    "summary": d.get("summary"),
    "flags": d.get("flags"),
    "openddf_overlaps": d.get("overlaps"),
    "decision_trace_summary": d.get("decision_trace_summary"),
    "segment_rows": rows,
    "timeline_issues": timeline_issues,
    "overflow_zero_analysis": overflow_zero_analysis,
    "counts": {
        "segments": len(rows),
        "adaptation_executed": sum(1 for r in rows if r["adaptation_executed"]),
        "reported_overflow_gt0": sum(1 for r in rows if int(r["overflow_ms_overlap"] or 0) > 0),
        "derived_overflow_gt0": sum(
            1 for r in rows if (r["tts_vs_slot_overflow"] or 0) > 0
        ),
        "audio_bleed_events": sum(
            1 for t in timeline_issues if t["type"] == "audio_bleeds_into_next_slot"
        ),
        "slot_overlap_events": sum(
            1 for t in timeline_issues if t["type"] == "slot_overlap"
        ),
        "false_zero_overflow": sum(
            1
            for x in overflow_zero_analysis
            if x["interpretation"] == "REPORTED_ZERO_BUT_TTS_STILL_OVERFLOWS_SLOT"
        ),
    },
}

(OUT / "g_timing_audit.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)

print("task", report["task_id"])
print("counts", json.dumps(report["counts"], indent=2))
print("false_zero_overflow samples:")
for x in overflow_zero_analysis:
    if x["interpretation"] != "TRUE_FIT":
        print(x)
print("timeline_issues (first 15):")
for t in timeline_issues[:15]:
    print(t)
print("wrote", OUT / "g_timing_audit.json")
