import zipfile, json, io, re

z = zipfile.ZipFile(r'c:\Users\serhii\Desktop\ы.zip')
names = z.namelist()
d = json.loads(z.read('openddf_full_report.json'))
out = []
out.append("FILES: " + ", ".join(names))
out.append("=== llm_diagnostics ===")
out.append(json.dumps(d.get('llm_diagnostics'), ensure_ascii=False))
out.append("=== adaptation_capabilities ===")
out.append(json.dumps(d.get('adaptation_capabilities'), ensure_ascii=False))
am = d.get('adaptation_mode') or {}
out.append("=== adaptation_mode.capabilities ===")
out.append(json.dumps(am.get('capabilities'), ensure_ascii=False))
le = d.get('llm_effectiveness') or {}
out.append("=== llm_effectiveness (scalars) ===")
out.append(json.dumps({k: v for k, v in le.items() if not isinstance(v, (list, dict))}, ensure_ascii=False))
q = d.get('post_tts_qa') or {}
out.append("=== post_tts_qa ===")
out.append(json.dumps({k: q.get(k) for k in ('checked','deviations','retries','fixed','adaptation_executed','duration_match')}, ensure_ascii=False))
rl = q.get('requires_llm_adaptation')
out.append("requires_llm gate: " + (json.dumps(rl, ensure_ascii=False) if rl else "None"))

# pipeline log LLM activity
try:
    plog = z.read('pipeline.log').decode('utf-8','replace')
    hits = [l for l in plog.splitlines() if re.search(r'(?i)(llm|rephrase|ollama|circuit|PostTTS)', l)]
    out.append("=== pipeline.log LLM lines: %d ===" % len(hits))
    out.extend(hits[:20])
except Exception as e:
    out.append("plog err: %s" % e)

cjk = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff]')
lat = re.compile(r'[A-Za-z]{3,}')
out.append("=== per-segment ===")
for s in d.get('segments', []):
    i = s['index']
    ft = s.get('final_tts_text') or ''
    tflag = 'CJK!' if cjk.search(ft) else ('LAT!' if lat.search(ft) else '')
    out.append(f"seg {i:2d}: slot={s.get('slot_ms')} actual={s.get('actual_duration_ms')} diff={s.get('speech_difference_ms')} match={s.get('duration_match_score')} post_tts={s.get('optimization_retries',{}).get('post_tts')} llm_called={s.get('llm_called')} att={s.get('llm_attempts')} errs={[e.get('code') for e in (s.get('errors') or [])]} {tflag}")
    out.append(f"     FINAL: {ft}")

io.open(r'c:\Users\serhii\Desktop\VideoMonster_V2\_tmp_y_out.txt','w',encoding='utf-8').write("\n".join(out))
print("done", len(names))
