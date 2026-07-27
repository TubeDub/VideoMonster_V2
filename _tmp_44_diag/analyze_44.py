#!/usr/bin/env python3
"""Analyze 44 diagnostics bundle."""
import json
import re
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent
JSON44 = Path(r"c:\Users\serhii\Desktop\44.json")


def norm(s):
    if s is None:
        return ""
    return str(s).strip()


def is_strict_prefix(shorter, longer):
    a, b = norm(shorter), norm(longer)
    if not a or not b or a == b:
        return False
    return b.startswith(a)


def neighbor_match(text, translations, idx):
    t = norm(text)
    if not t:
        return []
    matches = []
    for i, tr in enumerate(translations):
        if i == idx:
            continue
        trn = norm(tr)
        if not trn:
            continue
        if t == trn:
            matches.append({"type": "exact", "neighbor_idx": i})
        elif len(t) >= 20 and (t in trn or trn in t):
            matches.append({"type": "partial", "neighbor_idx": i})
    return matches


def detect_phrase_loop(text, min_repeats=3):
    t = norm(text)
    if not t:
        return False
    # repeated phrase of 3+ words
    words = t.split()
    if len(words) < min_repeats * 2:
        return False
    for n in range(3, min(8, len(words) // min_repeats + 1)):
        for start in range(len(words) - n * min_repeats + 1):
            phrase = " ".join(words[start : start + n])
            if len(phrase) < 8:
                continue
            count = t.count(phrase)
            if count >= min_repeats:
                return True
    # "у той момент" style
    if re.search(r"(.+?)(?:,\s*\1){2,}", t):
        return True
    return False


def main():
    seg_diag = json.loads((BASE / "segment_diagnostics.json").read_text(encoding="utf-8"))
    report = json.loads((BASE / "report.json").read_text(encoding="utf-8"))
    snap_after = json.loads((BASE / "snapshot_after.json").read_text(encoding="utf-8"))
    j44 = json.loads(JSON44.read_text(encoding="utf-8"))

    print("=== REPORT TOP ===")
    print("stage:", report.get("stage"))
    print("developer:", {k: report["developer"].get(k) for k in ("stage", "qa_ok", "issue_count", "segment_count") if "developer" in report})
    for k in ("exception", "error", "errors", "failure", "stacktrace"):
        if k in report and report[k]:
            print(f"{k}:", str(report[k])[:800])
    stack = (BASE / "stacktrace.txt").read_text(encoding="utf-8", errors="replace")
    print("stacktrace.txt:", repr(stack))

    # issues from report developer
    if "developer" in report:
        issues = report["developer"].get("issues") or report["developer"].get("issue_summary") or []
        if issues:
            print("developer issues sample:", issues[:5] if isinstance(issues, list) else issues)

    translations = [norm(s.get("translated_text")) for s in seg_diag]
    print(f"\nSegment count: {len(seg_diag)}")

    rows = []
    for s in seg_diag:
        idx = s.get("index")
        orig = norm(s.get("original_text"))
        tr = norm(s.get("translated_text"))
        ada = norm(s.get("text_after_adaptation"))
        pre = norm(s.get("pre_tts_text"))
        fin = norm(s.get("final_tts_text"))
        raw_tr = norm(s.get("raw_translation"))

        trunc_tr = is_strict_prefix(fin, tr)
        trunc_ada = is_strict_prefix(fin, ada)
        trunc_pre = is_strict_prefix(fin, pre)
        trunc_raw = is_strict_prefix(fin, raw_tr)

        pl_seg = bool(s.get("phrase_loop") or s.get("phrase_loop_healed"))
        pl_fin = detect_phrase_loop(fin)
        pl_tr = detect_phrase_loop(tr)
        pl_raw = detect_phrase_loop(raw_tr)

        for field in ("variant_log", "transformation_chain", "optimization_stages", "adaptation_stages", "warnings"):
            val = s.get(field)
            if val and "phrase_loop" in json.dumps(val, ensure_ascii=False).lower():
                pl_seg = True

        rows.append(
            {
                "idx0": idx,
                "idx1": (idx + 1) if idx is not None else None,
                "lens": {
                    "original": len(orig),
                    "translated": len(tr),
                    "after_adapt": len(ada),
                    "pre_tts": len(pre),
                    "final_tts": len(fin),
                    "raw_translation": len(raw_tr),
                },
                "trunc": {"vs_translated": trunc_tr, "vs_adaptation": trunc_ada, "vs_pre_tts": trunc_pre, "vs_raw": trunc_raw},
                "neigh": neighbor_match(fin, translations, idx),
                "phrase_loop": pl_seg or pl_fin,
                "phrase_loop_fin": pl_fin,
                "phrase_loop_tr": pl_tr,
                "phrase_loop_raw": pl_raw,
                "adaptation_reasons": s.get("adaptation_reasons") or [],
                "warnings": s.get("warnings") or [],
                "quality_reasons": s.get("quality_reasons"),
                "adapt_skip": s.get("adaptation_skip_reason"),
                "adapt_status": s.get("adaptation_status"),
                "adapt_executed": s.get("adaptation_executed"),
                "algorithm_reason": s.get("algorithm_reason"),
                "fin_eq_tr": fin == tr,
                "fin_eq_pre": fin == pre,
                "overflow_ms": (s.get("overlap_info") or {}).get("overflow_ms"),
                "final_tts_text": fin[:120],
                "translated_text": tr[:120],
            }
        )

    print("\n=== SEGMENT TABLE ===")
    for r in rows:
        l = r["lens"]
        tr_any = any(r["trunc"].values())
        print(
            f"Seg {r['idx0']:2d} (1-based {r['idx1']:2d}): "
            f"orig={l['original']:3d} tr={l['translated']:3d} ada={l['after_adapt']:3d} "
            f"pre={l['pre_tts']:3d} fin={l['final_tts']:3d} raw={l['raw_translation']:3d} | "
            f"trunc={tr_any} {r['trunc']} | neigh={r['neigh']} | "
            f"pl={r['phrase_loop']} (fin={r['phrase_loop_fin']}) | "
            f"warn={r['warnings']} adapt_reasons={r['adaptation_reasons']} quality={r['quality_reasons']} | "
            f"adapt={r['adapt_status']} skip={r['adapt_skip']} overflow={r['overflow_ms']}"
        )

    trunc_segs = [r for r in rows if any(r["trunc"].values())]
    neigh_segs = [r for r in rows if r["neigh"]]
    pl_segs = [r for r in rows if r["phrase_loop"]]
    print(f"\nTruncation: {len(trunc_segs)} -> {[r['idx0'] for r in trunc_segs]}")
    print(f"Neighbor bleed: {len(neigh_segs)} -> {[(r['idx0'], r['neigh']) for r in neigh_segs]}")
    print(f"Phrase loop: {len(pl_segs)} -> {[r['idx0'] for r in pl_segs]}")

    print("\n=== SNAPSHOT AFTER MISMATCHES ===")
    snap_by_idx = {s.get("index"): s for s in snap_after}
    mismatches = []
    for s in seg_diag:
        idx = s.get("index")
        snap = snap_by_idx.get(idx, {})
        fin = norm(s.get("final_tts_text"))
        fields = {
            "text": norm(snap.get("text")),
            "tts_text": norm(snap.get("tts_text")),
            "plain_text": norm(snap.get("plain_text")),
        }
        issues = []
        for k, v in fields.items():
            if v and fin and v != fin:
                issues.append({"field": k, "snap": v[:100], "final_tts": fin[:100]})
        for a, b in (("text", "tts_text"), ("text", "plain_text"), ("tts_text", "plain_text")):
            va, vb = fields[a], fields[b]
            if va and vb and va != vb:
                issues.append({"field": f"{a}_vs_{b}", "snap": va[:100], "other": vb[:100]})
        if issues:
            mismatches.append((idx, issues))
    if not mismatches:
        print("No mismatches vs final_tts_text (text/tts_text/plain_text all absent or equal)")
    for idx, issues in mismatches:
        print(f"Seg {idx}:")
        for iss in issues:
            print(f"  {iss}")

    print("\n=== 44.json vs segment_diagnostics ===")
    j44_segs = {s.get("index"): s for s in j44.get("segments", [])}
    for s in seg_diag:
        idx = s.get("index")
        j = j44_segs.get(idx, {})
        diffs = []
        for field in ("translated_text", "final_tts_text", "text_after_adaptation", "adaptation_executed", "adaptation_status"):
            dv = s.get(field)
            jv = j.get(field)
            if dv != jv:
                diffs.append((field, str(dv)[:80], str(jv)[:80]))
        if diffs:
            print(f"Seg {idx}:")
            for f, a, b in diffs:
                print(f"  {f}: zip={a!r} json44={b!r}")

    warn_c = Counter()
    adapt_c = Counter()
    skip_c = Counter()
    status_c = Counter()
    for r in rows:
        for w in r["warnings"]:
            warn_c[str(w)] += 1
        for a in r["adaptation_reasons"]:
            adapt_c[str(a)] += 1
        skip_c[r["adapt_skip"] or ""] += 1
        status_c[r["adapt_status"] or ""] += 1

    print("\n=== AGGREGATES ===")
    print("warnings:", dict(warn_c))
    print("adaptation_reasons:", dict(adapt_c))
    print("adaptation_skip_reason:", dict(skip_c))
    print("adaptation_status:", dict(status_c))
    print("quality_reasons segments:", [(r["idx0"], r["quality_reasons"]) for r in rows if r["quality_reasons"]])

    # export json for report writer
    out = {
        "rows": rows,
        "mismatches": [{"idx": i, "issues": iss} for i, iss in mismatches],
        "report_stage": report.get("stage"),
        "developer": report.get("developer", {}),
        "stacktrace": stack,
    }
    (BASE / "analysis_output.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nWrote analysis_output.json")


if __name__ == "__main__":
    main()
