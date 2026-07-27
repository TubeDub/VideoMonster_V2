#!/usr/bin/env python3
import json
import re
from pathlib import Path

BASE = Path(__file__).parent
JSON44 = Path(r"c:\Users\serhii\Desktop\44.json")
OUT = BASE / "analysis_full.json"


def norm(s):
    return "" if s is None else str(s).strip()


def is_strict_prefix(shorter, longer):
    a, b = norm(shorter), norm(longer)
    return bool(a and b and a != b and b.startswith(a))


def neighbor_match(text, translations, idx):
    t = norm(text)
    if not t:
        return []
    out = []
    for i, tr in enumerate(translations):
        if i == idx:
            continue
        trn = norm(tr)
        if not trn:
            continue
        if t == trn:
            out.append({"type": "exact", "neighbor_idx": i})
        elif len(t) >= 15 and t in trn:
            out.append({"type": "contained_in_neighbor", "neighbor_idx": i})
        elif len(trn) >= 15 and trn in t:
            out.append({"type": "contains_neighbor", "neighbor_idx": i})
    return out


def detect_phrase_loop(text, min_repeats=3):
    t = norm(text)
    if not t:
        return False
    if re.search(r"(.+?)(?:,\s*\1){2,}", t):
        return True
    for pat in [r"у той момент", r"коли́ ві́н", r"коли ві́н"]:
        if len(re.findall(pat, t, re.I)) >= min_repeats:
            return True
    return False


def main():
    seg_diag = json.loads((BASE / "segment_diagnostics.json").read_text(encoding="utf-8"))
    report = json.loads((BASE / "report.json").read_text(encoding="utf-8"))
    snap_after = json.loads((BASE / "snapshot_after.json").read_text(encoding="utf-8"))
    final_qa = json.loads((BASE / "final_dub_qa.json").read_text(encoding="utf-8"))
    j44 = json.loads(JSON44.read_text(encoding="utf-8"))

    translations = [norm(s.get("translated_text")) for s in seg_diag]
    snap_by = {s.get("index"): s for s in snap_after}
    j44_by = {s.get("index"): s for s in j44.get("segments", [])}

    rows = []
    for s in seg_diag:
        idx = s.get("index")
        fields = {
            "original": norm(s.get("original_text")),
            "translated": norm(s.get("translated_text")),
            "after_adapt": norm(s.get("text_after_adaptation")),
            "pre_tts": norm(s.get("pre_tts_text")),
            "final_tts": norm(s.get("final_tts_text")),
            "raw": norm(s.get("raw_translation")),
        }
        lens = {k: len(v) for k, v in fields.items()}
        snap = snap_by.get(idx, {})
        snap_text = norm(snap.get("text"))

        trunc = {
            "final_vs_translated": is_strict_prefix(fields["final_tts"], fields["translated"]),
            "final_vs_pre": is_strict_prefix(fields["final_tts"], fields["pre_tts"]),
            "translated_vs_raw": is_strict_prefix(fields["translated"], fields["raw"]),
            "final_longer_than_translated": len(fields["final_tts"]) > len(fields["translated"]) + 5,
        }
        neigh = neighbor_match(fields["final_tts"], translations, idx)
        # prefix bleed: final starts with prev segment translated
        prefix_bleed = []
        if idx and idx > 0:
            prev = norm(seg_diag[idx - 1].get("translated_text"))
            if prev and fields["final_tts"].startswith(prev):
                prefix_bleed.append({"from_idx": idx - 1, "prefix_len": len(prev)})

        rows.append(
            {
                "idx0": idx,
                "idx1": idx + 1,
                "lens": lens,
                "trunc": trunc,
                "neigh": neigh,
                "prefix_bleed": prefix_bleed,
                "phrase_loop": {
                    "final": detect_phrase_loop(fields["final_tts"]),
                    "pre": detect_phrase_loop(fields["pre_tts"]),
                    "raw": detect_phrase_loop(fields["raw"]),
                },
                "adaptation_reasons": s.get("adaptation_reasons") or [],
                "warnings": s.get("warnings") or [],
                "quality_reasons": s.get("quality_reasons"),
                "adapt_skip": s.get("adaptation_skip_reason"),
                "adapt_status": s.get("adaptation_status"),
                "adapt_executed": s.get("adaptation_executed"),
                "overflow_ms": (s.get("overlap_info") or {}).get("overflow_ms"),
                "underflow_hint": s.get("speech_difference_ms"),
                "snap_text_len": len(snap_text),
                "snap_matches_final": snap_text == fields["final_tts"],
                "snap_matches_pre": snap_text == fields["pre_tts"],
                "snap_matches_translated": snap_text == fields["translated"],
                "pre_vs_final_same": fields["pre_tts"] == fields["final_tts"],
                "after_adapt_vs_final_same": fields["after_adapt"] == fields["final_tts"],
                "json44_adapt_executed": j44_by.get(idx, {}).get("adaptation_executed"),
            }
        )

    summary = {
        "report_stage": report.get("stage"),
        "qa_ok": report.get("developer", {}).get("qa_ok"),
        "issue_count": report.get("developer", {}).get("issue_count"),
        "segment_count": len(seg_diag),
        "exception": report.get("exception"),
        "stacktrace": (BASE / "stacktrace.txt").read_text(encoding="utf-8", errors="replace"),
        "trunc_final_vs_translated": [r["idx0"] for r in rows if r["trunc"]["final_vs_translated"]],
        "trunc_translated_vs_raw": [r["idx0"] for r in rows if r["trunc"]["translated_vs_raw"]],
        "final_longer_than_translated": [r["idx0"] for r in rows if r["trunc"]["final_longer_than_translated"]],
        "neighbor_bleed": [r["idx0"] for r in rows if r["neigh"]],
        "prefix_bleed": [r["idx0"] for r in rows if r["prefix_bleed"]],
        "phrase_loop_final": [r["idx0"] for r in rows if r["phrase_loop"]["final"]],
        "snap_mismatch_final": [r["idx0"] for r in rows if r["snap_text_len"] and not r["snap_matches_final"]],
        "snap_mismatch_pre": [r["idx0"] for r in rows if r["snap_text_len"] and not r["snap_matches_pre"]],
        "pre_final_mismatch": [r["idx0"] for r in rows if not r["pre_vs_final_same"]],
        "all_adapt_not_executed": sum(1 for r in rows if r["adapt_status"] == "ADAPTATION NOT EXECUTED"),
        "all_skip_fits_no_change": sum(1 for r in rows if r["adapt_skip"] == "FitsNoChange"),
        "final_qa_codes": {},
        "rows": rows,
    }
    codes = {}
    for iss in final_qa.get("issues", []):
        c = iss.get("code", "unknown")
        codes[c] = codes.get(c, 0) + 1
    summary["final_qa_codes"] = codes

    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
