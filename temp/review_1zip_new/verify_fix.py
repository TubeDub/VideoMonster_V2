# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts
from engines.translation_validation import is_shared_mt_blob_reclaim
from engines.tts_text_guard import repair_neighbor_bleed

data = json.loads(Path(r"c:\Users\serhii\Desktop\1.json").read_text(encoding="utf-8"))
pairs = [(6, 7), (8, 9), (14, 15), (16, 17)]
lines = []
for a, b in pairs:
    lines.append(f"=== {a},{b} ===")
    segs = []
    for idx in (a, b):
        s = data["segments"][idx]
        seg = {
            "index": idx,
            "final_text": s.get("translated_text") or s.get("pre_tts_text"),
            "translated_text": s.get("translated_text"),
            "voice_input": s.get("final_tts_text"),
            "semantic_engine_text": s.get("semantic_engine_text"),
            "semantic_text": s.get("semantic_engine_text"),
            "raw_translation": s.get("raw_translation"),
            "original_text": s.get("original_text"),
            "tts_text": s.get("final_tts_text"),
            "text": s.get("final_tts_text"),
            "plain_text": s.get("final_tts_text"),
        }
        owned = resolve_segment_text_for_tts(seg)
        was = s.get("final_tts_text") or ""
        pre = s.get("pre_tts_text") or ""
        lines.append(
            f"  resolve {idx}: was={len(was)} now={len(owned)} "
            f"reclaim={is_shared_mt_blob_reclaim(s.get('translated_text') or '', was)} "
            f"eq_pre={owned.rstrip('.!?…') == pre.rstrip('.!?…')}"
        )
        lines.append(f"    now={owned[:100]}")
        segs.append(seg)
    report = repair_neighbor_bleed(segs)
    lines.append(f"  repair healed={report.get('healed')} actions={report.get('actions')}")
    for seg in segs:
        lines.append(f"    after_repair[{seg['index']}]={str(seg.get('tts_text'))[:100]}")

out = Path(__file__).with_name("verify_fix_out.txt")
out.write_text("\n".join(lines), encoding="utf-8")
print(out.read_text(encoding="utf-8"))
