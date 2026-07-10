"""Structured per-segment translation pipeline stage logging."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.translation_stage")

_STAGE_LOG_NAME = "translation_stage.log"
_STAGE_LATEST_NAME = "translation_stage_latest.log"


def _esc(text: str, limit: int = 240) -> str:
    return (text or "").replace("\n", " ").replace("\t", " ").strip()[:limit]


def _stage_log_dir(app_dir: Path | str) -> Path:
    log_dir = Path(app_dir) / "output" / "dev"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _append_stage_log(app_dir: Path | str, line: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    full = f"[{ts}] {line}"
    logger.info("[TR-PIPE] %s", line)
    log_dir = _stage_log_dir(app_dir)
    for name in (_STAGE_LOG_NAME, _STAGE_LATEST_NAME):
        with (log_dir / name).open("a", encoding="utf-8") as f:
            f.write(full + "\n")


def log_start(
    app_dir: Path | str,
    *,
    engine: str,
    route: str,
    src_lang: str,
    tgt_lang: str,
    segment_count: int,
    task_id: str = "",
    mode: str = "",
) -> None:
    """START marker for UniversalTranslationPipeline.translate_segments."""
    _append_stage_log(
        app_dir,
        f"START TRANSLATION task={task_id} engine={engine} route={route} "
        f"src={src_lang} tgt={tgt_lang} segments={segment_count} mode={mode}",
    )


def log_end(
    app_dir: Path | str,
    *,
    engine: str,
    route: str,
    elapsed_sec: float,
    translated_ok: int,
    segment_count: int,
    task_id: str = "",
    error: str = "",
) -> None:
    """END marker for UniversalTranslationPipeline.translate_segments."""
    status = "OK" if not error else "FAILED"
    _append_stage_log(
        app_dir,
        f"END TRANSLATION task={task_id} status={status} engine={engine} route={route} "
        f"elapsed={elapsed_sec:.3f}s ok={translated_ok}/{segment_count} error={error or '-'}",
    )


def log_timing_summary(
    app_dir: Path | str,
    task_id: str,
    stages: dict[str, float],
) -> None:
    """Pipeline timing summary — Whisper / Marian / LLM / Validation / TTS."""
    from engines.translation_timing import format_duration_clock

    parts = []
    labels = {
        "whisper": "Whisper",
        "marian": "Marian",
        "llm_adaptation": "Qwen adaptation",
        "validation": "Validation",
        "tts": "TTS",
        "timing": "Timing",
        "mux": "Mux",
    }
    for key, sec in stages.items():
        if sec and float(sec) > 0:
            label = labels.get(key, key)
            parts.append(f"{label}: {format_duration_clock(sec)}")
    if parts:
        _append_stage_log(
            app_dir,
            f"TIMING SUMMARY task={task_id} | " + " | ".join(parts),
        )


def log_llm_timeout_debug(
    app_dir: Path | str,
    task_id: str,
    **fields: Any,
) -> None:
    """Debug: LLM/orchestrator timeout with request size, attempt, URL."""
    try:
        from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

        if not IS_DEBUG_LEARNING_MODE():
            return
    except Exception:
        return

    lines = [
        f"LLM TIMEOUT DEBUG task={task_id}",
        f"source={fields.get('source', 'llm')}",
        f"agent={fields.get('agent', '-')}",
        f"attempt={fields.get('attempt', 0)}",
        f"wait_sec={fields.get('wait_sec', 0)}",
        f"wall_timeout_sec={fields.get('wall_timeout_sec', fields.get('timeout_sec', '-'))}",
        f"llm_call_timeout_sec={fields.get('llm_call_timeout_sec', '-')}",
        f"chars={fields.get('chars_sent', fields.get('chars', 0))}",
        f"segment={fields.get('segment', fields.get('inflight_segment', '-'))}",
        f"model={fields.get('model', '-')}",
        f"provider={fields.get('provider', '-')}",
        f"api_url={fields.get('api_url', '-')}",
        f"failure_phase={fields.get('failure_phase', fields.get('error', '-'))}",
    ]
    _append_stage_log(app_dir, "\n".join(lines))


def log_debug_timing_breakdown(
    app_dir: Path | str,
    task_id: str,
    timing: dict[str, Any],
) -> None:
    """Debug block: [Marian] segments / sec / avg per segment."""
    segment_count = int(timing.get("segment_count") or 0)
    stats = timing.get("segment_stats") or {}
    blocks: list[str] = []
    for title, key in (
        ("Marian", "marian_mt"),
        ("Qwen", "llm_adaptation"),
        ("Post", "post_processing"),
    ):
        row = stats.get(key) or {}
        sec = float(row.get("sec") or (timing.get("ui_buckets") or {}).get(key) or 0)
        seg = int(row.get("segments") or segment_count or 0)
        avg = float(row.get("avg_sec_per_segment") or (sec / max(seg, 1)))
        lines = [f"[{title}]", f"{seg} segments", f"{sec:.1f} sec"]
        if key == "llm_adaptation" and seg > 0 and sec > 0:
            lines.append(f"Average: {avg:.2f} sec/segment")
        blocks.append("\n".join(lines))
    body = "\n\n".join(blocks)
    _append_stage_log(app_dir, f"DEBUG TIMING task={task_id}\n{body}")


def log_segment(
    app_dir: Path | str,
    *,
    segment_index: int,
    engine: str,
    route: str,
    elapsed_ms: float,
    ok: bool,
    text_len: int,
) -> None:
    """Per-group MT timing line during translation."""
    _append_stage_log(
        app_dir,
        f"SEGMENT idx={segment_index} engine={engine} route={route} "
        f"ms={elapsed_ms:.1f} ok={ok} len={text_len}",
    )


def text_fingerprint(*parts: str) -> str:
    blob = "\x1e".join(str(p or "").strip() for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def log_translation_stage(
    task_id: str | None,
    *,
    stage: str,
    segment_index: int | None = None,
    segment_id: str | None = None,
    text: str | None = None,
    source_lang: str | None = None,
    target_lang: str | None = None,
    detail: str | None = None,
    changed: bool | None = None,
) -> None:
    """Log one pipeline stage value for a segment (Translation TZ §1)."""
    parts = [
        f"task={task_id or '?'}",
        f"stage={stage}",
    ]
    if segment_index is not None:
        parts.append(f"idx={segment_index}")
    if segment_id:
        parts.append(f"segment_id={segment_id}")
    if source_lang:
        parts.append(f"src={source_lang}")
    if target_lang:
        parts.append(f"tgt={target_lang}")
    if changed is not None:
        parts.append(f"changed={changed}")
    if detail:
        parts.append(f"detail={detail}")
    if text is not None:
        parts.append(f"text={_esc(text)!r}")
        parts.append(f"fp={text_fingerprint(text)}")
    logger.info("[TR-STAGE] %s", " ".join(parts))


def log_translation_stage_batch(
    task_id: str | None,
    *,
    stage: str,
    texts: list[str],
    source_lang: str | None = None,
    target_lang: str | None = None,
    detail: str | None = None,
) -> None:
    for i, text in enumerate(texts):
        log_translation_stage(
            task_id,
            stage=stage,
            segment_index=i,
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            detail=detail,
        )


def log_tts_request(
    task_id: str | None,
    *,
    segment_index: int,
    segment_id: str | None,
    language_code: str,
    voice_id: str,
    provider: str,
    text: str,
    group_index: int | None = None,
) -> None:
    """Log TTS call parameters before synthesis (Translation TZ §4)."""
    parts = [
        f"task={task_id or '?'}",
        "event=tts_request",
        f"idx={segment_index}",
        f"language_code={language_code}",
        f"voice_id={voice_id}",
        f"provider={provider}",
        f"text={_esc(text)!r}",
        f"fp={text_fingerprint(text)}",
    ]
    if segment_id:
        parts.append(f"segment_id={segment_id}")
    if group_index is not None:
        parts.append(f"group={group_index}")
    logger.info("[TR-TTS] %s", " ".join(parts))
