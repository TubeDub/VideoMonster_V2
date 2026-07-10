"""Developer reports for Smart Segment Optimizer V2."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from engines.smart_segment_optimizer.optimizer import SegmentOptimizeResult


def save_dev_report(
    app_dir: Path,
    reports: list[SegmentOptimizeResult],
    meta: dict[str, Any],
    *,
    task_id: str = "",
    prosody_groups: list[dict] | None = None,
) -> str:
    log_dir = app_dir / "output" / "dev" / "smart_segment_optimizer"
    log_dir.mkdir(parents=True, exist_ok=True)
    jid = uuid.uuid4().hex[:10]
    json_path = log_dir / f"sso_{task_id or jid}.json"
    txt_path = log_dir / f"sso_{task_id or jid}.txt"
    latest = log_dir / "sso_latest.json"

    prosody_by_head: dict[int, dict] = {}
    for pg in prosody_groups or []:
        indices = pg.get("indices") or []
        if indices:
            prosody_by_head[int(indices[0])] = pg

    seg_payload = []
    for r in reports:
        d = r.to_dict()
        pg = prosody_by_head.get(r.index)
        if pg:
            d["text_for_tts"] = pg.get("text_for_tts") or d.get("text_for_tts")
            d["prosody"] = pg
        seg_payload.append(d)

    payload = {
        "meta": meta,
        "segments": seg_payload,
        "prosody_groups": prosody_groups or [],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

    lines = [
        f"=== SMART SEGMENT OPTIMIZER V2 task={task_id} ===",
        f"segments={meta.get('segments')} changed={meta.get('changed')} "
        f"skipped={meta.get('skipped')} underfill={meta.get('underfill', 0)}",
        "",
    ]
    for r in reports:
        pg = prosody_by_head.get(r.index)
        lines.append(f"--- Segment #{r.index + 1} ---")
        lines.append(f"Original segment duration: {r.segment_ms} ms")
        lines.append(f"Original Translation: {r.original}")
        if r.skipped:
            lines.append(
                f"SKIPPED: {r.skip_reason} (est={r.est_ms_before}ms fill={r.fill_percent_before}%)"
            )
            if pg:
                lines.append(f"Text for TTS: {str(pg.get('text_for_tts', ''))[:400]}")
            lines.append("")
            continue
        lines.append(f"Optimized Translation: {r.optimized}")
        if pg:
            lines.append(f"Text for TTS: {str(pg.get('text_for_tts', ''))[:400]}")
        lines.append(f"Level: {r.level_used} ({r.stop_reason})")
        lines.append(f"Est TTS BEFORE: {r.est_ms_before} ms")
        lines.append(f"Est TTS AFTER: {r.est_ms_after} ms")
        lines.append(f"Fill %: {r.fill_percent_before}% → {r.fill_percent_after}%")
        diff = r.diff or {}
        if diff.get("removed_words"):
            lines.append(f"Removed: {', '.join(diff['removed_words'][:20])}")
        if diff.get("replaced_words"):
            pairs = [f"{x['from']}→{x['to']}" for x in diff["replaced_words"][:15]]
            lines.append(f"Replaced: {', '.join(pairs)}")
        if pg:
            for pause in pg.get("pauses") or []:
                lines.append(f"Pause added: {pause}")
            for acc in pg.get("accents") or []:
                lines.append(f"Accent: {acc}")
        for step in r.steps:
            if step.get("applied"):
                lines.append(f"  L{step['level']} {step.get('name')}: {step.get('reason', '')}")
        q = r.quality or {}
        lines.append(f"Quality: ok={q.get('ok')} score={q.get('score', '—')}")
        lines.append("")

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    (log_dir / "sso_latest.txt").write_text("\n".join(lines), encoding="utf-8")
    return str(json_path)
