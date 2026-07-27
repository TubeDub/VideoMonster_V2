# -*- coding: utf-8 -*-
import json
from pathlib import Path

base = Path(r"C:\Users\serhii\Desktop\VideoMonster_V2\temp\review_1zip_new")
data = json.loads(Path(r"c:\Users\serhii\Desktop\1.json").read_text(encoding="utf-8"))
qa = json.loads((base / "final_dub_qa.json").read_text(encoding="utf-8"))
segs = data["segments"]
critical = [6, 7, 8, 9, 14, 15, 16, 17, 21, 22]
lines = []
lines.append(f"task={data['task_id']} lang={data['target_lang']} n={len(segs)}")
lines.append(f"qa_ok={qa.get('ok')} issues={qa.get('issue_count')}")
lines.append(f"summary={json.dumps(data.get('summary'), ensure_ascii=False)}")
lines.append(f"llm_available={data.get('llm_effectiveness',{}).get('llm_available')}")
lines.append(f"post_tts_qa counts: checked={data['post_tts_qa'].get('checked')} ok={data['post_tts_qa'].get('ok')} failed={data['post_tts_qa'].get('failed')} resegmented={data['post_tts_qa'].get('resegmented')} rewritten={data['post_tts_qa'].get('rewritten')}")
lines.append("")
for i in critical:
    s = segs[i]
    dec = s.get("adaptation_decision") or {}
    lines.append("=" * 70)
    lines.append(f"SEG {i} decision={dec.get('decision')} overflow={dec.get('overflow_ms')} underflow={dec.get('underflow_ms')}")
    lines.append(f"times {s.get('start_time_ms')}->{s.get('end_time_ms')} slot={s.get('original_duration_ms')} tts_dec={dec.get('tts_duration_ms')} actual={s.get('actual_duration_ms')} final_tts_dur={s.get('final_tts_duration_ms')}")
    lines.append(f"path_chain={s.get('path_chain')}")
    lines.append(f"algorithm_reason={s.get('algorithm_reason')}")
    lines.append(f"merge_info={json.dumps(s.get('merge_info'), ensure_ascii=False)[:400]}")
    lines.append(f"overlap_info={json.dumps(s.get('overlap_info'), ensure_ascii=False)[:400]}")
    lines.append(f"warnings={s.get('warnings')}")
    lines.append(f"errors={s.get('errors')}")
    lines.append(f"ORIG : {s.get('original_text')}")
    lines.append(f"RAW  : {s.get('raw_translation')}")
    lines.append(f"TR   : {s.get('translated_text')}")
    lines.append(f"ADAPT: {s.get('text_after_adaptation')}")
    lines.append(f"PRE  : {s.get('pre_tts_text')}")
    lines.append(f"FINAL: {s.get('final_tts_text')}")
    lines.append(f"semantic_engine_text: {s.get('semantic_engine_text')}")

lines.append("\n=== text_tts_mismatch issues ===")
for iss in qa.get("issues") or []:
    if iss.get("code") == "text_tts_mismatch":
        lines.append(json.dumps(iss, ensure_ascii=False, indent=2))

lines.append("\n=== post_tts_qa issues ===")
for iss in (data.get("post_tts_qa") or {}).get("issues") or []:
    lines.append(json.dumps(iss, ensure_ascii=False))

# zero actual duration segments
zeros = [s["index"] for s in segs if not s.get("actual_duration_ms")]
lines.append(f"\nzero actual_duration_ms: {zeros}")

# language validator
lv = (base / "language_validator.log").read_text(encoding="utf-8", errors="replace")
lines.append("\n=== language_validator.log ===")
lines.append(lv)

out = base / "critical_report.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("wrote", out, "bytes", out.stat().st_size)
