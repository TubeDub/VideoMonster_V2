"""
Developer-only diagnostic logging for TubeDub pipeline.
Not exposed in UI — logs live in output/dev/.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


def _fmt_ms(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    return f"{s // 60:02d}:{s % 60:02d}.{int(ms) % 1000:03d}"


def _timing_line(idx: int, item: Any, text: str = "") -> str:
    if isinstance(item, dict):
        start, end = int(item.get("start", 0)), int(item.get("end", 0))
    elif isinstance(item, (list, tuple)) and len(item) >= 2:
        start, end = int(item[0]), int(item[1])
    elif isinstance(item, str):
        return f"idx={idx} time={item} text={text[:120]!r}"
    else:
        start, end = 0, 0
    dur = max(0, end - start)
    preview = f" text={text[:120]!r}" if text else ""
    return (
        f"idx={idx} start={start} end={end} dur_ms={dur} "
        f"({_fmt_ms(start)}-{_fmt_ms(end)}){preview}"
    )


class DevDiagnostics:
    """Per-task developer logs: segmentation, OCR, translation, TTS, timing_map."""

    SECTIONS = (
        "segmentation",
        "ocr",
        "translation",
        "translation_pipeline",
        "tts",
        "timing_map",
        "video_integrity",
        "pipeline_audit",
        "overlap_quality",
        "word_timing",
        "live",
        "streaming",
        "broadcast_dub",
        "media_browser",
        "recording",
        "voice_training",
        "vocal_training",
        "assistant",
    )

    def __init__(self, task_id: str, app_dir: Path):
        self.task_id = task_id
        self.log_dir = app_dir / "output" / "dev"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._paths: dict[str, str] = {}

    def paths(self) -> dict[str, str]:
        return dict(self._paths)

    def _write(self, section: str, lines: list[str]) -> str:
        path = self.log_dir / f"{section}_{self.task_id}.log"
        ts = datetime.now(timezone.utc).isoformat()
        header = f"=== {section.upper()} task={self.task_id} ts={ts} ===\n"
        body = "\n".join(lines) + "\n"
        path.write_text(header + body, encoding="utf-8")
        self._paths[section] = str(path)
        return str(path)

    def log_segmentation(
        self,
        *,
        raw_segments: Sequence[str],
        raw_timing: Sequence[Any],
        merged_segments: Sequence[str],
        merged_timing: Sequence[Any],
        source: str,
    ) -> str:
        lines = [
            f"text_source={source}",
            f"raw_count={len(raw_segments)} merged_count={len(merged_segments)}",
            "",
            "--- RAW (before merge_stt_segments) ---",
        ]
        for i, seg in enumerate(raw_segments):
            tm = raw_timing[i] if i < len(raw_timing) else {}
            lines.append(_timing_line(i, tm, str(seg)))

        lines.extend(["", "--- MERGED (after merge_stt_segments) ---"])
        for i, seg in enumerate(merged_segments):
            tm = merged_timing[i] if i < len(merged_timing) else {}
            lines.append(_timing_line(i, tm, str(seg)))

        if len(raw_segments) != len(merged_segments):
            lines.append("")
            lines.append(
                f"merge_ratio={len(raw_segments)}->{len(merged_segments)} "
                f"({100 * (1 - len(merged_segments) / max(len(raw_segments), 1)):.0f}% reduction)"
            )
        return self._write("segmentation", lines)

    def log_ocr(self, *, text_source: str, note: str = "") -> str:
        lines = [
            "OCR: NOT USED",
            "TubeDub does not extract on-screen text for dubbing.",
            f"Active text_source={text_source}",
            "Allowed sources: whisper_stt | preloaded_subtitles | studio_redub",
            "OCR text is never injected into TTS or translation.",
        ]
        if note:
            lines.append(f"note={note}")
        return self._write("ocr", lines)

    def log_translation(
        self,
        *,
        source_lang: str,
        target_lang: str,
        source_segments: Sequence[str],
        translated_segments: Sequence[str],
        skip_translate: bool,
        method: str,
    ) -> str:
        lines = [
            f"source_lang={source_lang} target_lang={target_lang}",
            f"skip_translate={skip_translate} method={method}",
            f"segment_count={len(source_segments)}",
            "",
            "--- SOURCE -> TRANSLATED ---",
        ]
        n = max(len(source_segments), len(translated_segments))
        for i in range(n):
            src = str(source_segments[i]) if i < len(source_segments) else ""
            tgt = str(translated_segments[i]) if i < len(translated_segments) else ""
            lines.append(f"[{i}] SRC: {src[:200]}")
            lines.append(f"[{i}] TGT: {tgt[:200]}")
            if src.strip() and tgt.strip() and src.strip().lower() == tgt.strip().lower():
                lines.append(f"[{i}] WARN: identical src/tgt (possible untranslated line)")
            lines.append("")

        if skip_translate:
            lines.append("INFO: preloaded translation reused — no second translate pass.")
        else:
            lines.append("INFO: single batch translate via translation_naturalizer (no double translate).")
        return self._write("translation", lines)

    def log_tts(
        self,
        *,
        voice: str,
        groups: Sequence[dict[str, Any]],
        segment_files: Sequence[dict[str, Any]],
    ) -> str:
        lines = [
            f"voice={voice}",
            f"group_count={len(groups)} placed_segments={len(segment_files)}",
            "",
            "--- TTS GROUPS ---",
        ]
        for g_idx, group in enumerate(groups):
            indices = group.get("indices", [])
            text = str(group.get("text") or "")
            timing = group.get("timing")
            lines.append(
                f"group={g_idx} indices={indices} timing={timing} "
                f"text_len={len(text)} text={text[:160]!r}"
            )

        lines.extend(["", "--- PLACED SEGMENTS ---"])
        for row in segment_files:
            lines.append(json.dumps(row, ensure_ascii=False))

        lines.append("")
        lines.append("INFO: TTS reads translated_segments only — never OCR or raw on-screen text.")
        return self._write("tts", lines)

    def log_timing_map(
        self,
        *,
        timing_map: Sequence[Any],
        segments: Sequence[str] | None = None,
        video_duration_ms: int | None = None,
        timing_fit_warnings: Sequence[str] | None = None,
    ) -> str:
        lines = [
            f"slot_count={len(timing_map)}",
            f"video_duration_ms={video_duration_ms}",
            "",
            "--- TIMING MAP ---",
        ]
        segs = segments or [""] * len(timing_map)
        for i, tm in enumerate(timing_map):
            text = str(segs[i]) if i < len(segs) else ""
            lines.append(_timing_line(i, tm, text))

        if timing_fit_warnings:
            lines.extend(["", "--- TIMING_FIT WARNINGS ---"])
            lines.extend(str(w) for w in timing_fit_warnings)

        total_span = 0
        if timing_map:
            last = timing_map[-1]
            if isinstance(last, dict):
                total_span = int(last.get("end", 0))
            elif isinstance(last, (list, tuple)) and len(last) >= 2:
                total_span = int(last[1])

        if video_duration_ms and total_span:
            drift = total_span - video_duration_ms
            lines.append("")
            lines.append(f"last_segment_end_ms={total_span} drift_vs_video_ms={drift}")

        return self._write("timing_map", lines)

    def log_video_integrity(self, report: dict[str, Any]) -> str:
        lines = [
            f"ok={report.get('ok')}",
            f"source={report.get('source')}",
            f"output={report.get('output')}",
            "",
            "--- CHECKS ---",
        ]
        for name, detail in (report.get("checks") or {}).items():
            lines.append(f"{name}: {json.dumps(detail, ensure_ascii=False)}")

        warnings = report.get("warnings") or []
        if warnings:
            lines.extend(["", "--- WARNINGS ---"])
            lines.extend(str(w) for w in warnings)

        errors = report.get("errors") or []
        if errors:
            lines.extend(["", "--- ERRORS ---"])
            lines.extend(str(e) for e in errors)

        return self._write("video_integrity", lines)

    def log_pipeline_audit(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """
        Построчный аудит: segment_id, speech_text, ocr_text, translated_text, tts_text.
        """
        lines = [
            "segment_id\tspeech_text\tocr_text\ttranslated_text\ttts_text",
        ]
        for row in rows:
            sid = row.get("segment_id", row.get("index", ""))
            lines.append(
                f"{sid}\t"
                f"{row.get('speech_text', '')}\t"
                f"{row.get('ocr_text', '')}\t"
                f"{row.get('translated_text', '')}\t"
                f"{row.get('tts_text', '')}"
            )
        if extra:
            lines.extend(["", "--- META ---"])
            for k, v in extra.items():
                lines.append(f"{k}={v}")
        return self._write("pipeline_audit", lines)

    def log_overlap_quality(self, report: dict[str, Any]) -> str:
        lines = [
            f"ok={report.get('ok')}",
            f"pre_issues={report.get('pre_analysis_count', 0)}",
            f"fitted_overlaps={report.get('fitted_overlap_count', 0)}",
            "",
            "--- UNRESOLVED ---",
        ]
        for item in report.get("unresolved_overlaps") or []:
            lines.append(
                f"idx={item.get('idx')} next={item.get('next_idx')} "
                f"overflow_ms={item.get('overflow_ms')} strategy={item.get('strategy_used')}"
            )
        lines.extend(["", "--- PRE ANALYSIS (TTS vs window) ---"])
        for item in report.get("pre_issues") or []:
            if item.get("overflow_ms", 0) <= 40:
                continue
            lines.append(
                f"idx={item.get('idx')} tts_ms={item.get('tts_ms')} window={item.get('window_ms')} "
                f"overflow={item.get('overflow_ms')} strategy={item.get('recommended_strategy')}"
            )
        lines.extend(["", "--- FITTED PLACEMENTS ---"])
        for item in report.get("fitted_placements") or []:
            lines.append(
                f"idx={item.get('idx')} start={item.get('place_start')} "
                f"fitted_ms={item.get('fitted_ms')} strategy={item.get('strategy')} "
                f"overflow_ms={item.get('overflow_ms')} atempo={item.get('atempo')}"
            )
        return self._write("overlap_quality", lines)

    def log_translation_pipeline_report(
        self,
        *,
        source_lang: str,
        target_lang: str,
        audits: Sequence[dict[str, Any]],
        translate_meta: dict[str, Any] | None = None,
        router_summary: dict[str, Any] | None = None,
    ) -> str:
        """Dev report: Original → Raw MT → Engine → Score → Semantic → Final → TTS."""
        lines = [
            f"source_lang={source_lang} target_lang={target_lang}",
            f"segment_count={len(audits)}",
            "",
            "=== PIPELINE CHAIN (per segment) ===",
            "idx | whisper | raw_mt | engine | quality_score | mt_retries | "
            "naturalized | semantic | final | tts | router_reason",
        ]
        for row in sorted(audits, key=lambda x: int(x.get("index", 0))):
            idx = row.get("index", "")
            lines.append(
                f"[{idx}] engine={row.get('engine', '')} "
                f"score={row.get('quality_score', 0)} retries={row.get('mt_retries', 0)} "
                f"router={row.get('router_reason', '')}"
            )
            lines.append(f"  ORIGINAL: {str(row.get('whisper_text', ''))[:240]}")
            lines.append(f"  RAW MT:   {str(row.get('raw_translation', ''))[:240]}")
            lines.append(f"  NATURAL:  {str(row.get('naturalized_text', ''))[:240]}")
            lines.append(f"  SEMANTIC: {str(row.get('semantic_text', ''))[:240]}")
            lines.append(f"  FINAL:    {str(row.get('final_text', ''))[:240]}")
            lines.append(f"  TTS:      {str(row.get('tts_text', ''))[:240]}")
            lines.append(
                f"  TIMING ms: mt={row.get('duration_ms', 0)} "
                f"nat={row.get('naturalizer_ms', 0)} llm={row.get('llm_ms', 0)} "
                f"quality={row.get('quality_pass_ms', 0)} semantic={row.get('semantic_ms', 0)}"
            )
            lines.append("")

        if translate_meta:
            lines.extend(["=== TRANSLATE META ==="])
            for k, v in translate_meta.items():
                lines.append(f"{k}={v}")

        if router_summary:
            lines.extend(["", "=== ROUTER / ENGINE STATS ==="])
            lines.append(json.dumps(router_summary, ensure_ascii=False, indent=2))

        return self._write("translation_pipeline", lines)

    def log_word_timing(self, info: dict[str, Any]) -> str:
        from engines.word_timing_map.phase0 import format_dev_inspector_block

        lines = [
            format_dev_inspector_block(info),
            "",
        ]
        cps = (info.get("word_timing_checkpoints") or {}).get("checkpoints") or []
        if cps:
            lines.append("--- Phase 0 checkpoints ---")
            for cp in cps:
                status = "OK" if cp.get("ok") else "FAIL"
                lines.append(
                    f"[{status}] {cp.get('stage')}: segments={cp.get('segment_count')} "
                    f"words={cp.get('words_total')} real={cp.get('real_segments')}"
                )
                for issue in cp.get("issues") or []:
                    lines.append(f"  ! {issue}")
        return self._write("word_timing", lines)
