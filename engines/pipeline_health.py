"""Pipeline Health — per-stage invariant checks."""

from __future__ import annotations

from typing import Any

from engines.placeholder_guard import detect_placeholder_leaks, placeholder_health


def check_stage(
    *,
    stage: str,
    text_in: str,
    text_out: str,
    original: str = "",
    token_map: dict[str, str] | None = None,
    src_lang: str = "",
    tgt_lang: str = "",
) -> dict[str, Any]:
    """Run health checks after a pipeline stage."""
    from engines.translation_quality_score import compute_quality_metrics

    ph = placeholder_health(text_out, stage=stage, token_map=token_map)
    metrics: dict[str, Any] = {}
    if original.strip() and text_out.strip():
        metrics = compute_quality_metrics(
            original, text_out, src_lang=src_lang, tgt_lang=tgt_lang
        )

    issues = list(ph.get("issues") or [])
    if metrics.get("raw_equals_whisper"):
        issues.append("untranslated")
    if float(metrics.get("english_word_pct") or 0) > 25:
        issues.append(f"english_leak:{metrics.get('english_word_pct')}%")
    if float(metrics.get("mixed_language_pct") or 0) > 15:
        issues.append(f"mixed_lang:{metrics.get('mixed_language_pct')}%")
    if metrics.get("missing_preserved_tokens", 0) > 0:
        issues.append("missing_names")

    changed = str(text_in or "").strip() != str(text_out or "").strip()
    return {
        "stage": stage,
        "ok": len(issues) == 0,
        "changed": changed,
        "issues": issues,
        "placeholder_leaks": ph.get("placeholder_leaks") or [],
        "english_word_pct": metrics.get("english_word_pct", ph.get("english_word_pct")),
        "quality_metrics": {
            k: metrics[k]
            for k in (
                "english_word_pct",
                "mixed_language_pct",
                "translated_pct",
                "missing_preserved_tokens",
                "placeholder_leak_count",
            )
            if k in metrics
        },
    }


def merge_health(*checks: dict[str, Any]) -> dict[str, Any]:
    stages = [c for c in checks if c]
    return {
        "ok": all(c.get("ok", True) for c in stages),
        "stages": stages,
        "issue_count": sum(len(c.get("issues") or []) for c in stages),
    }
