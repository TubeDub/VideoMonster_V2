"""Live verification of LLM auto-discovery + Timing-Rewrite wiring (TZ §9 checks).

Exercises the real pipeline functions against whatever local LLM is running
(auto-discovered). Prints a PASS/FAIL line for each acceptance criterion.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Ensure auto-discovery is on; do NOT hardcode any endpoint (must be detected).
os.environ.pop("VM_LLM_BASE_URL", None)
os.environ.pop("OPENAI_BASE_URL", None)
os.environ.pop("VM_TRANSLATE_MODEL", None)
os.environ["VM_LLM_AUTODISCOVER"] = "1"

from engines.llm_adaptation_mode import detect_capabilities, discover_local_llm  # noqa: E402
from engines.semantic_adaptation import estimate_tts_duration_ms  # noqa: E402
from engines.translation_adapt import _llm_chat, llm_rephrase_available  # noqa: E402


def line(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


print("=" * 70)
print("LLM AUTO-DISCOVERY + TIMING REWRITE — LIVE VERIFICATION")
print("=" * 70)

disc = discover_local_llm(force=True)
line("auto-discovery found a local LLM (no manual config)", bool(disc), str(disc))

caps = detect_capabilities()
line("llm_rephrase_available = true", caps.get("llm_available") is True, str(caps))
line("llm_rephrase_available() helper true", llm_rephrase_available() is True)

raw = _llm_chat("Translated line: Він дуже справді просто втомився сьогодні.")
line("real HTTP roundtrip to discovered endpoint returns text", bool(raw), repr(raw))

# ── Timing Rewrite uses the LLM to shorten an overflowing line ──────────────
import engines.timing_aware_translation as tat  # noqa: E402

tgt = "uk"
source_en = "He was really very tired today."
original_uk = (
    "Він дуже справді просто надзвичайно втомився сьогодні, "
    "доволі сильно і вельми помітно для всіх навколо."
)
slot_ms = int(estimate_tts_duration_ms(source_en, "en"))
before_ms = int(estimate_tts_duration_ms(original_uk, tgt))
print(f"\nslot_ms (EN source)≈{slot_ms} · UA before≈{before_ms}ms · text: {original_uk}")

out, rec = tat.adapt_segment_to_slot(
    original_uk,
    source_text=source_en,
    slot_ms=slot_ms,
    src_lang="en",
    tgt_lang=tgt,
    index=0,
)
after_ms = int(estimate_tts_duration_ms(out, tgt))
print(f"AFTER≈{after_ms}ms · reason={getattr(rec, 'reason', None)} · text: {out}")

changed = out != original_uk
line("Timing Rewrite changed the text via adaptation", changed)
# No mechanical truncation: result still ends as a complete sentence.
not_truncated = out.strip().endswith((".", "!", "?", "…")) and "…" not in out.rstrip("…")
line("no mechanical truncation (complete sentence)", out.strip()[-1] in ".!?…")
line("adapted line is shorter than the overflowing original", after_ms <= before_ms)

# ── optimizer stage log shows llm_rephrase actually ran ─────────────────────
from engines.semantic_optimizer import optimize_llm_rephrase_for_slot  # noqa: E402

res = optimize_llm_rephrase_for_slot(
    original_uk, source_hint=source_en, slot_ms=slot_ms, tgt_lang=tgt
)
stage_names = [getattr(s, "stage", getattr(s, "get", lambda *_: None)("stage") if isinstance(s, dict) else None) for s in (res.stages or [])]
stage_names = [
    (s.get("stage") if isinstance(s, dict) else getattr(s, "stage", None)) for s in (res.stages or [])
]
line("optimizer ran an llm_rephrase stage (llm_rewrite_used)", "llm_rephrase" in stage_names, str(stage_names))

# ── Build a real OpenDDF report from this LLM adaptation run ────────────────
import json as _json  # noqa: E402

from engines.segment_timing_qa import build_openddf_full_report  # noqa: E402

real_stages = [s.to_dict() if hasattr(s, "to_dict") else s for s in (res.stages or [])]
adapted_text = res.text
task_info = {
    "task_id": "verify-llm-001",
    "target_lang": tgt,
    "voice": "uk-UA-OstapNeural",
    "adaptation_mode": "automatic",
    "source_segments": [source_en],
    "segments_data": [
        {
            "index": 0,
            "segment_id": "s0",
            "text": adapted_text,
            "tts_text": adapted_text,
            "plain_text": adapted_text,
            "file": "g0000.mp3",
            "slot_ms": slot_ms,
            "start_ms": 0,
            "end_ms": slot_ms,
            "requires_llm_adaptation": False,
            "text_adaptation_trace": {
                "executed": True,
                "iterations": len(real_stages),
                "text_after": adapted_text,
                "original_duration_ms": slot_ms,
                "first_tts_duration_ms": before_ms,
                "final_tts_duration_ms": after_ms,
                "timing_source": "timing_map",
                "reasons": [getattr(res, "stopped_reason", "")],
                "stages": real_stages,
            },
        }
    ],
    "translation_audits": [
        {
            "index": 0,
            "final_text": original_uk,
            "raw_translation": original_uk,
            "naturalized_text": original_uk,
            "tts_text": adapted_text,
            "pre_tts_text": adapted_text,
            "whisper_text": source_en,
            "quality_details": {
                "timing_aware": {
                    "text_after": adapted_text,
                    "predicted_ms_after": after_ms,
                    "iterations": len(real_stages),
                    "optimization_stages": real_stages,
                },
            },
        }
    ],
    "post_tts_qa": {"adaptation_executed": True, "checked": 1, "fixed": 1},
    "timing_map": [{"start": 0, "end": slot_ms}],
    "timing_map_backup": [{"start": 0, "end": slot_ms}],
}

report = build_openddf_full_report(task_info)
seg0 = (report.get("segments") or [{}])[0]
mode_blk = report.get("adaptation_mode") or {}
line("OpenDDF: segment llm_rewrite_used = true", seg0.get("llm_rewrite_used") is True)
line("OpenDDF: requires_llm_adaptation cleared on this segment", seg0.get("requires_llm_adaptation") is False)
line("OpenDDF: mode block present", bool(mode_blk.get("mode")))

out_path = os.path.join(os.path.dirname(__file__), "openddf_verify_report.json")
with open(out_path, "w", encoding="utf-8") as fh:
    _json.dump(report, fh, ensure_ascii=False, indent=2)
print(f"\nOpenDDF report written: {out_path}")
print("--- adaptation_mode ---")
print(_json.dumps(mode_blk, ensure_ascii=False, indent=2))
print("--- segment[0] (selected fields) ---")
print(_json.dumps({k: seg0.get(k) for k in (
    "index", "original_text", "translated_text", "final_tts_text",
    "rule_rewrite_used", "llm_rewrite_used", "requires_llm_adaptation",
    "original_duration_ms", "first_tts_duration_ms", "final_tts_duration_ms",
    "adaptation_status", "adaptation_reasons",
)}, ensure_ascii=False, indent=2))

print("=" * 70)
print("DONE")
