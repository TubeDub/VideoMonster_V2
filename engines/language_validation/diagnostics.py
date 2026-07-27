# -*- coding: utf-8 -*-
"""Write Language Validation artifacts into Diagnostic ZIP / task diagnostics dir."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.language_validation.diagnostics")


def write_language_validation_diagnostics(
    *,
    task_id: str,
    app_dir: str | Path,
    stage: str,
    decisions: list[dict[str, Any]] | None = None,
    recovery: dict[str, Any] | None = None,
    confidence_rows: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Persist language_validator.log / confidence_scores.json / recovery_trace.json /
    decision_trace.json under ``output/diagnostics/<task_id>/``.

    Returns map of artifact name → path. Passive are also candidates for PassiveOpenDDF zip.
    """
    root = Path(app_dir)
    out = root / "output" / "diagnostics" / str(task_id)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    paths: dict[str, str] = {}

    decisions = list(decisions or [])
    recovery = dict(recovery or {})
    confidence_rows = list(confidence_rows or [])

    # confidence_scores.json
    conf_path = out / "confidence_scores.json"
    conf_payload = {
        "task_id": task_id,
        "stage": stage,
        "created_at": ts,
        "rows": confidence_rows
        or [
            {
                "index": d.get("index"),
                "expected": d.get("target_lang") or d.get("expected_lang"),
                "detected": d.get("detected_lang"),
                "confidence": d.get("confidence"),
                "target_confidence": d.get("target_confidence"),
                "scores": d.get("scores") or {},
                "code": d.get("code"),
                "category": d.get("category"),
            }
            for d in decisions
        ],
    }
    conf_path.write_text(
        json.dumps(conf_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["confidence_scores.json"] = str(conf_path)

    # recovery_trace.json
    rec_path = out / "recovery_trace.json"
    rec_payload = {
        "task_id": task_id,
        "stage": stage,
        "created_at": ts,
        **recovery,
    }
    rec_path.write_text(
        json.dumps(rec_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["recovery_trace.json"] = str(rec_path)

    # decision_trace.json — tree of stages
    dec_path = out / "decision_trace.json"
    tree = []
    for d in decisions:
        tree.append(
            {
                "index": d.get("index"),
                "segment_id": d.get("segment_id"),
                "tree": [
                    "Translation",
                    "Naturalizer",
                    "Meaning Fit",
                    "Language Validator",
                    f"Confidence={d.get('confidence')}",
                    f"Category={d.get('category')}",
                    f"Decision={'pass' if d.get('ok') else 'fail'}",
                    "Recovery" if (d.get('recovery_actions') or []) else "NoRecovery",
                    f"Final={d.get('code') or 'ok'}",
                ],
                "decision_trace": d.get("decision_trace") or [],
                "message": d.get("message") or "",
                "expected_lang": d.get("target_lang") or d.get("expected_lang"),
                "detected_lang": d.get("detected_lang"),
                "reasons": d.get("reasons") or [],
            }
        )
    dec_payload = {
        "task_id": task_id,
        "stage": stage,
        "created_at": ts,
        "segments": tree,
        "extra": extra or {},
    }
    dec_path.write_text(
        json.dumps(dec_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["decision_trace.json"] = str(dec_path)

    # language_validator.log — human-readable
    log_path = out / "language_validator.log"
    lines = [
        f"# TubeDub Language Validator log",
        f"# task={task_id} stage={stage} at={ts}",
        "",
    ]
    for d in decisions:
        lines.append("=" * 60)
        lines.append(d.get("message") or json.dumps(d, ensure_ascii=False)[:500])
        lines.append(
            f"index={d.get('index')} category={d.get('category')} "
            f"hard_fail={d.get('hard_fail')} ok={d.get('ok')}"
        )
        lines.append("")
    if recovery:
        lines.append("=" * 60)
        lines.append("RECOVERY SUMMARY")
        lines.append(
            f"healed={recovery.get('recovered')} hard_left={recovery.get('failed_hard')}"
        )
        for row in recovery.get("trace") or []:
            lines.append(json.dumps(row, ensure_ascii=False))
    log_path.write_text("\n".join(lines), encoding="utf-8")
    paths["language_validator.log"] = str(log_path)

    logger.info(
        "Language validation diagnostics written task=%s stage=%s files=%s",
        task_id,
        stage,
        list(paths),
    )
    return paths
