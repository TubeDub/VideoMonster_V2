# -*- coding: utf-8 -*-
import json
import collections
import re
from pathlib import Path

base = Path(r"C:\Users\serhii\Desktop\VideoMonster_V2\temp\review_1zip_new")
data = json.loads(Path(r"c:\Users\serhii\Desktop\1.json").read_text(encoding="utf-8"))
qa = json.loads((base / "final_dub_qa.json").read_text(encoding="utf-8"))

codes = collections.Counter(i.get("code") for i in qa.get("issues") or [])
sev = collections.Counter(i.get("severity") for i in qa.get("issues") or [])
print("ISSUE CODES", dict(codes))
print("SEVERITY", dict(sev))

by = {}
for i in qa.get("issues") or []:
    by.setdefault(i.get("code"), []).append(i)
for c, items in by.items():
    print("---", c, "n=", len(items))
    print(json.dumps(items[0], ensure_ascii=False)[:500])
    if len(items) > 1:
        print(json.dumps(items[1], ensure_ascii=False)[:300])

segs = data["segments"]
print("\nN SEGS", len(segs), "skipped", data.get("skipped_segments"))
olaps = data.get("overlaps")
print(
    "overlaps field",
    type(olaps).__name__,
    len(olaps) if isinstance(olaps, list) else olaps,
)
if isinstance(olaps, list) and olaps:
    print("overlap sample", json.dumps(olaps[0], ensure_ascii=False)[:500])
    print("overlap sample2", json.dumps(olaps[1], ensure_ascii=False)[:500] if len(olaps) > 1 else None)


def norm(t):
    return re.sub(r"\s+", " ", (t or "").strip().lower())


problems = []
for s in segs:
    idx = s["index"]
    tr = s.get("translated_text") or ""
    adapt = s.get("text_after_adaptation") or ""
    final = s.get("final_tts_text") or ""
    trunc = bool(tr) and bool(final) and final != tr and tr.startswith(final.rstrip(".!?,;: "))
    longer = len(final) > len(tr) * 1.15 + 5
    words = final.split()
    loop = False
    if len(words) >= 8:
        for n in (4, 5, 6):
            for i in range(len(words) - 2 * n + 1):
                chunk = " ".join(words[i : i + n])
                rest = " ".join(words[i + n :])
                if chunk and chunk in rest:
                    loop = True
                    break
            if loop:
                break
    bleed = None
    for other in segs:
        if other["index"] == idx:
            continue
        ot = other.get("translated_text") or ""
        if len(ot) > 40 and ot[:40] in final and ot[:40] not in tr:
            bleed = other["index"]
            break
    dec = s.get("adaptation_decision") or {}
    problems.append(
        {
            "idx": idx,
            "decision": dec.get("decision"),
            "overflow_ms": dec.get("overflow_ms"),
            "underflow_ms": dec.get("underflow_ms"),
            "tts_ms": dec.get("tts_duration_ms") or s.get("final_tts_duration_ms"),
            "slot_ms": dec.get("original_duration_ms")
            or s.get("slot_ms")
            or s.get("original_duration_ms"),
            "start": s.get("start_time_ms"),
            "end": s.get("end_time_ms"),
            "llm_available": dec.get("llm_available"),
            "llm_called": s.get("llm_called"),
            "llm_rewrite_used": s.get("llm_rewrite_used"),
            "rule_rewrite": s.get("rule_rewrite_used"),
            "tr_eq_final": norm(tr) == norm(final),
            "adapt_eq_final": norm(adapt) == norm(final),
            "trunc": trunc,
            "longer": longer,
            "loop": loop,
            "bleed": bleed,
            "warnings": s.get("warnings"),
            "errors": s.get("errors"),
            "overlap_info": s.get("overlap_info"),
            "compression_ratio": s.get("compression_ratio"),
            "duration_match_score": s.get("duration_match_score"),
            "speech_diff": s.get("speech_difference_ms"),
            "actual_dur": s.get("actual_duration_ms"),
            "final_tts_dur": s.get("final_tts_duration_ms"),
            "path_chain": s.get("path_chain"),
            "algorithm_reason": s.get("algorithm_reason"),
            "tr": tr,
            "final": final,
            "adapt": adapt,
            "orig": s.get("original_text") or "",
            "pre": s.get("pre_tts_text") or "",
        }
    )

print("\nPER-SEG")
for p in problems:
    flags = []
    if p["trunc"]:
        flags.append("TRUNC")
    if p["longer"]:
        flags.append("LONGER")
    if p["loop"]:
        flags.append("LOOP")
    if p["bleed"] is not None:
        flags.append(f"BLEED->{p['bleed']}")
    if not p["tr_eq_final"]:
        flags.append("TEXT_DIFF")
    if p["overlap_info"]:
        flags.append("OLAP")
    flag_s = ",".join(flags) if flags else "-"
    print(
        f"{p['idx']:2d} dec={str(p['decision']):<20} ov={p['overflow_ms']} un={p['underflow_ms']} "
        f"tts={p['tts_ms']} slot={p['slot_ms']} actual={p['actual_dur']} final_tts_dur={p['final_tts_dur']} "
        f"llm={p['llm_called']}/{p['llm_rewrite_used']} flags={flag_s} "
        f"score={p['duration_match_score']} cr={p['compression_ratio']} speech_diff={p['speech_diff']}"
    )

# text diffs detail
print("\nTEXT_DIFF DETAIL")
for p in problems:
    if not p["tr_eq_final"] or p["trunc"] or p["loop"] or p["bleed"] is not None:
        print(f"--- idx {p['idx']}")
        print("ORIG :", p["orig"][:200])
        print("TR   :", p["tr"][:200])
        print("ADAPT:", p["adapt"][:200])
        print("PRE  :", p["pre"][:200])
        print("FINAL:", p["final"][:200])

print("\nllm_effectiveness", json.dumps(data.get("llm_effectiveness"), ensure_ascii=False)[:1200])
print("adaptation_capabilities", json.dumps(data.get("adaptation_capabilities"), ensure_ascii=False)[:1200])
print("adaptation_mode", data.get("adaptation_mode"))
print("pre_tts_integrity", json.dumps(data.get("pre_tts_integrity"), ensure_ascii=False)[:1500])
print("tts_pipeline", json.dumps(data.get("tts_pipeline"), ensure_ascii=False)[:1500])
print("ai_installation", json.dumps(data.get("ai_installation"), ensure_ascii=False)[:1000])
print("decision_trace_summary", json.dumps(data.get("decision_trace_summary"), ensure_ascii=False)[:1500])
print("post_tts_qa full", json.dumps(data.get("post_tts_qa"), ensure_ascii=False)[:4000])

# placement timeline continuity
print("\nTIMELINE")
prev_end = None
for p in sorted(problems, key=lambda x: x["start"] or 0):
    gap = None if prev_end is None else (p["start"] or 0) - prev_end
    ov = None if prev_end is None else prev_end - (p["start"] or 0)
    print(
        f"idx={p['idx']:2d} {p['start']}->{p['end']} gap_from_prev={gap} "
        f"overlap_prev={ov if ov and ov>0 else 0} slot={p['slot_ms']} tts={p['tts_ms']} actual={p['actual_dur']}"
    )
    prev_end = p["end"]

# write compact report
out = {
    "task_id": data.get("task_id"),
    "qa_ok": False,
    "issue_count": qa.get("issue_count"),
    "issue_codes": dict(codes),
    "severity": dict(sev),
    "summary": data.get("summary"),
    "flags": data.get("flags"),
    "post_tts_qa": data.get("post_tts_qa"),
    "segments": [
        {
            "idx": p["idx"],
            "decision": p["decision"],
            "overflow_ms": p["overflow_ms"],
            "underflow_ms": p["underflow_ms"],
            "tts_ms": p["tts_ms"],
            "slot_ms": p["slot_ms"],
            "actual_dur": p["actual_dur"],
            "final_tts_dur": p["final_tts_dur"],
            "tr_eq_final": p["tr_eq_final"],
            "trunc": p["trunc"],
            "loop": p["loop"],
            "bleed": p["bleed"],
            "path_chain": p["path_chain"],
            "algorithm_reason": p["algorithm_reason"],
            "orig": p["orig"],
            "tr": p["tr"],
            "final": p["final"],
        }
        for p in problems
    ],
}
(base / "analysis_compact.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("\nWrote analysis_compact.json")
