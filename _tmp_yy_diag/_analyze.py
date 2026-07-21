import json
from pathlib import Path

desk = Path(r"c:/Users/serhii/Desktop")
root = Path(r"c:/Users/serhii/Desktop/VideoMonster_V2/_tmp_yy_diag")
data = json.loads((desk / "йй.json").read_text(encoding="utf-8"))
rep = json.loads((root / "report.json").read_text(encoding="utf-8"))

print("TASK", data.get("task_id"))
print("REPORT_TASK", rep.get("task_id"))
print("ERROR", rep.get("developer", {}).get("error_code"))
msg = str(rep.get("exception", {}).get("message") or "")
print("EXCEPTION_SNIP", msg[:300])

qa = data.get("post_tts_qa") or {}
print(
    "POST_TTS",
    {
        k: qa.get(k)
        for k in (
            "checked",
            "ok",
            "failed",
            "adaptation_executed",
            "avg_timing_score",
            "segments_score_ge_95",
        )
    },
)
req = qa.get("requires_llm_adaptation") or {}
print("REQUIRES_LLM", req.get("count"), req.get("segment_indices"))

budgets = qa.get("budgets") or []
print("\n#  slot   tts  delta  status                 score  reason")
for b in budgets:
    slot = int(b.get("slot_duration") or 0)
    tts = int(b.get("measured_duration") or 0)
    delta = slot - tts
    print(
        f"{int(b.get('index',0))+1:2d} {slot:6d} {tts:6d} {delta:6d}  "
        f"{str(b.get('final_status') or b.get('status') or '-'):22s} "
        f"{float(b.get('timing_score') or 0):5.1f}  "
        f"{str(b.get('rewrite_reason') or '')[:40]}"
    )

# highlight key segs
print("\nKEY:")
for idx in (5, 6):  # 0-based: #6 father/son, #7 hospital
    b = budgets[idx]
    print(
        f"  seg#{idx+1}: slot={b.get('slot_duration')} tts={b.get('measured_duration')} "
        f"under={b.get('underflow')} over={b.get('overflow')} status={b.get('final_status')}"
    )

segs = data.get("segments") or []
dsal_n = sum(1 for s in segs if s.get("dsal_applied") or s.get("adaptation_executed"))
print("segments_with_adaptation_flags", dsal_n, "/", len(segs))
print("summary", data.get("summary"))
print("final_dub_qa keys", list((data.get("final_dub_qa") or {}).keys())[:15])
fd = data.get("final_dub_qa") or {}
for k in ("overlap_count", "fitted_file_ok", "avg_timing_score", "ok"):
    if k in fd:
        print(" final", k, fd.get(k))
