"""Batch preparation for Professional Dubbing."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from engines.professional_dubbing.config import is_enabled, is_prosody_style
from engines.professional_dubbing.prosody import ProsodyPlan, build_prosody_plan
from engines.smart_segment_optimizer.timing import parse_timing_ms, segment_duration_ms

logger = logging.getLogger("tubedub.professional_dubbing")


def prepare_tts_groups_prosody(
    tts_groups: list[dict],
    *,
    lang: str = "ru",
    style_id: str = "modern",
    delivery: str = "",
    base_rate: str | None = None,
    base_pitch: str | None = None,
    segment_voice_hints: dict[int, dict[str, Any]] | None = None,
    use_ssml: bool = True,
    app_dir: Path | None = None,
    task_id: str = "",
    source_audio_path: str | Path | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    if not is_enabled() or not is_prosody_style(style_id, delivery):
        return list(tts_groups), {"enabled": False, "skipped": True}

    from engines.professional_dubbing.source_cues import extract_slot_cues

    app_dir = app_dir or Path(__file__).resolve().parent.parent.parent
    t0 = time.perf_counter()
    out: list[dict] = []
    plans: list[dict] = []

    for gi, group in enumerate(tts_groups):
        text = str(group.get("text") or "").strip()
        timing = group.get("timing") or [0, 5000]
        start, end = parse_timing_ms(timing)
        seg_ms = max(200, end - start)
        indices = group.get("indices") or [0]
        is_cont = int(indices[0]) > 0 if indices else False
        cues = extract_slot_cues(source_audio_path, start, end)
        head_idx = int(indices[0]) if indices else 0
        voice_hint = dict((segment_voice_hints or {}).get(head_idx) or {})
        effective_rate = str(voice_hint.get("rate") or base_rate or "").strip() or None
        effective_pitch = str(voice_hint.get("pitch") or base_pitch or "").strip() or None

        plan = build_prosody_plan(
            text,
            segment_ms=seg_ms,
            lang=lang,
            base_rate=effective_rate,
            base_pitch=effective_pitch,
            use_ssml=use_ssml,
            source_cues=cues,
            is_continuation=is_cont,
        )
        plans.append({
            "group_index": gi,
            "indices": group.get("indices"),
            "voice_hint": voice_hint,
            **plan.to_dict(),
        })

        g2 = dict(group)
        g2["text"] = plan.text_for_tts
        g2["plain_text"] = plan.plain_text
        g2["prosody_rate"] = plan.suggested_rate
        g2["prosody_pitch"] = plan.suggested_pitch
        g2["place_delay_ms"] = plan.place_delay_ms
        g2["lead_in_ms"] = plan.lead_in_ms
        g2["prosody"] = plan.to_dict()
        if voice_hint:
            g2["voice_direction"] = voice_hint
        out.append(g2)

    meta = {
        "enabled": True,
        "task_id": task_id,
        "groups": len(out),
        "style_id": style_id,
        "elapsed_sec": round(time.perf_counter() - t0, 3),
    }
    report_path = _save_report(app_dir, plans, meta, task_id=task_id)
    meta["dev_report_path"] = report_path
    logger.info("[ProDub] prosody on %d groups, %.2fs", len(out), meta["elapsed_sec"])
    return out, meta


def _save_report(
    app_dir: Path,
    plans: list[dict],
    meta: dict[str, Any],
    *,
    task_id: str = "",
) -> str:
    log_dir = app_dir / "output" / "dev" / "professional_dubbing"
    log_dir.mkdir(parents=True, exist_ok=True)
    jid = task_id or uuid.uuid4().hex[:10]
    json_path = log_dir / f"produb_{jid}.json"
    payload = {"meta": meta, "groups": plans}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (log_dir / "produb_latest.json").write_text(
        json_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    lines = [
        f"=== PROFESSIONAL DUBBING task={task_id} ===",
        f"groups={meta.get('groups')}",
        "",
    ]
    for p in plans:
        lines.append(f"--- Group {p.get('group_index', 0) + 1} indices={p.get('indices')} ---")
        lines.append(f"Original Translation: {p.get('plain_text', '')}")
        lines.append(f"Text for TTS: {p.get('text_for_tts', '')[:500]}")
        lines.append(f"Segment duration: {p.get('segment_ms')} ms")
        lines.append(f"Est TTS before: {p.get('est_ms_before')} ms")
        lines.append(f"Est TTS after: {p.get('est_ms_after')} ms")
        lines.append(f"Fill %: {p.get('fill_percent')}")
        lines.append(f"Rate: {p.get('suggested_rate')}")
        for pause in p.get("pauses") or []:
            lines.append(f"  Pause: {pause}")
        for acc in p.get("accents") or []:
            lines.append(f"  Accent: {acc}")
        for d in p.get("decisions") or []:
            lines.append(f"  Decision: {d}")
        lines.append("")
    (log_dir / f"produb_{jid}.txt").write_text("\n".join(lines), encoding="utf-8")
    (log_dir / "produb_latest.txt").write_text("\n".join(lines), encoding="utf-8")
    return str(json_path)
