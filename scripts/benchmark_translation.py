#!/usr/bin/env python
"""Benchmark all installed MT engines — Task №14."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from typing import Any

from engines.mt.registry import get_registry, save_pair_rankings
from engines.mt.qe import composite_benchmark_score
from engines.translation_quality_score import compute_quality_score


def _parse_pair(s: str) -> tuple[str, str]:
    a, b = s.split("->", 1)
    return a.strip(), b.strip()


def run_benchmark(*, include_nllb: bool = False) -> int:
    app_dir = ROOT
    cases_path = app_dir / "data" / "mt_benchmark_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8")).get("cases") or []

    engines = get_registry()
    if not include_nllb:
        engines = [e for e in engines if e.id != "nllb"]

    print("=== TubeDub MT Benchmark ===")
    print(f"Engines: {[e.id for e in engines]}")
    print(f"Cases: {len(cases)}\n")

    results: dict[str, dict[str, dict]] = {}
    pair_scores: dict[str, dict[str, list[float]]] = {}

    for case in cases:
        text = case["text"]
        cid = case["id"]
        for pair_str in case.get("pairs") or []:
            src, tgt = _parse_pair(pair_str)
            pk = f"{src}->{tgt}"
            results.setdefault(pk, {})
            for eng in engines:
                if not eng.supports_pair(src, tgt):
                    continue
                t0 = time.perf_counter()
                try:
                    mt = eng.translate(text, src, tgt)
                except Exception as e:
                    mt = type("R", (), {"text": "", "elapsed_ms": 0, "error": str(e)})()
                elapsed = (time.perf_counter() - t0) * 1000.0
                if not mt.text:
                    score = 0.0
                    metrics = {"error": getattr(mt, "error", "empty")}
                else:
                    score, metrics = compute_quality_score(
                        text, mt.text, src_lang=src, tgt_lang=tgt
                    )
                    ref = (case.get("references") or {}).get(pk)
                    if ref:
                        score = composite_benchmark_score(score, ref, mt.text)
                        metrics["reference_score"] = score
                entry = {
                    "case_id": cid,
                    "text": text,
                    "translation": mt.text,
                    "score": score,
                    "ms": round(elapsed, 1),
                    "metrics": metrics,
                }
                results[pk].setdefault(eng.id, []).append(entry)
                pair_scores.setdefault(pk, {}).setdefault(eng.id, []).append(score)
                print(f"[{pk}] {eng.id} | {cid} | score={score:.1f} | {elapsed:.0f}ms")
                print(f"  -> {mt.text[:120]}")
                print()

    rankings: dict[str, list[str]] = {}
    report_pairs: dict[str, Any] = {}

    engine_offline = {e.id: e.offline for e in engines}

    for pk, eng_scores in pair_scores.items():
        avg_by_eng: list[tuple[str, float]] = []
        report_pairs[pk] = {}
        for eid, scores in eng_scores.items():
            avg = sum(scores) / max(len(scores), 1)
            avg_by_eng.append((eid, avg))
            report_pairs[pk][eid] = {
                "avg_score": round(avg, 2),
                "runs": len(scores),
                "offline": engine_offline.get(eid, False),
            }
        # Production rule: offline engines first; online only as fallback tier.
        offline = sorted(
            [(e, s) for e, s in avg_by_eng if engine_offline.get(e, False)],
            key=lambda x: x[1],
            reverse=True,
        )
        online = sorted(
            [(e, s) for e, s in avg_by_eng if not engine_offline.get(e, False)],
            key=lambda x: x[1],
            reverse=True,
        )
        rankings[pk] = [e for e, _ in offline] + [e for e, _ in online]
        print(f"RANKING {pk}: {' > '.join(rankings[pk])}")

    save_pair_rankings(app_dir, rankings)

    out_dir = app_dir / "output" / "dev"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    report_path = out_dir / f"mt_benchmark_report_{ts}.json"
    report = {
        "timestamp": ts,
        "engines": [{"id": e.id, "name": e.name, "offline": e.offline} for e in engines],
        "rankings": rankings,
        "pair_summary": report_pairs,
        "details": results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = out_dir / "mt_benchmark_report.json"
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {report_path}")
    return 0


if __name__ == "__main__":
    inc_nllb = "--nllb" in sys.argv
    raise SystemExit(run_benchmark(include_nllb=inc_nllb))
