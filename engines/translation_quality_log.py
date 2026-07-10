"""Append-only translation quality audit log — output/dev/translation_quality.log"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class SegmentTranslationAudit:
    index: int
    source_lang: str
    target_lang: str
    whisper_text: str
    raw_translation: str
    naturalized_text: str = ""
    final_text: str = ""
    tts_text: str = ""
    engine: str = ""
    model: str = ""
    route: str = "direct"
    pivot: str | None = None
    duration_ms: float = 0.0
    group_indices: list[int] = field(default_factory=list)
    semantic_adapted: bool = False
    naturalizer_applied: bool = False
    naturalizer_executed: bool = False
    timing_aware_applied: bool = False
    timing_aware_executed: bool = False
    quality_pass_before: str = ""
    quality_pass_after: str = ""
    semantic_text: str = ""
    naturalizer_ms: float = 0.0
    llm_ms: float = 0.0
    quality_pass_ms: float = 0.0
    semantic_ms: float = 0.0
    quality_score: float = 0.0
    mt_retries: int = 0
    router_reason: str = ""
    route_label: str = ""
    quality_details: dict[str, Any] = field(default_factory=dict)
    validation_warnings: list[dict[str, Any]] = field(default_factory=list)
    naturalizer_reasons: list[str] = field(default_factory=list)
    nat_quality_score: float = 0.0
    nat_mixed_language_pct: float = 0.0
    nat_retry_reason: str = ""
    nat_problems: list[str] = field(default_factory=list)
    nat_fix_count: int = 0
    nat_restored_entities: list[str] = field(default_factory=list)
    nat_warnings: list[str] = field(default_factory=list)
    nat_retried: bool = False
    alternative_translation: str = ""
    alternative_route: str = ""
    alternative_engine: str = ""
    alternative_score: float = 0.0
    routes_tried: list[str] = field(default_factory=list)
    enterprise: bool = False
    tournament_engines: list[str] = field(default_factory=list)
    tournament_scores: dict[str, float] = field(default_factory=dict)
    fusion_reason: str = ""
    architect: dict[str, Any] = field(default_factory=dict)
    whisper_len: int = 0
    raw_len: int = 0
    naturalized_len: int = 0
    final_len: int = 0

    def to_log_line(self) -> str:
        def esc(s: str) -> str:
            return (s or "").replace("\t", " ").replace("\n", " ").strip()[:500]

        warn_codes = ",".join(
            f"{w.get('stage', '')}:{w.get('code', '')}" if w.get("stage") else str(w.get("code", ""))
            for w in (self.validation_warnings or [])[:12]
        ) or "-"

        return (
            f"idx={self.index}\t"
            f"src={self.source_lang}\t"
            f"tgt={self.target_lang}\t"
            f"whisper={esc(self.whisper_text)!r}\t"
            f"raw_mt={esc(self.raw_translation)!r}\t"
            f"naturalized={esc(self.naturalized_text)!r}\t"
            f"quality_before={esc(self.quality_pass_before)!r}\t"
            f"quality_after={esc(self.quality_pass_after)!r}\t"
            f"semantic={esc(self.semantic_text)!r}\t"
            f"final={esc(self.final_text)!r}\t"
            f"tts={esc(self.tts_text or self.final_text)!r}\t"
            f"engine={self.engine}\t"
            f"model={self.model}\t"
            f"route={self.route}\t"
            f"pivot={self.pivot or '-'}\t"
            f"semantic_adapted={int(self.semantic_adapted)}\t"
            f"mt_ms={self.duration_ms:.1f}\t"
            f"nat_ms={self.naturalizer_ms:.1f}\t"
            f"llm_ms={self.llm_ms:.1f}\t"
            f"quality_ms={self.quality_pass_ms:.1f}\t"
            f"semantic_ms={self.semantic_ms:.1f}\t"
            f"quality_score={self.quality_score:.1f}\t"
            f"mt_retries={self.mt_retries}\t"
            f"router={esc(self.router_reason)!r}\t"
            f"route={esc(self.route_label)!r}\t"
            f"w_len={self.whisper_len}\t"
            f"raw_len={self.raw_len}\t"
            f"nat_len={self.naturalized_len}\t"
            f"final_len={self.final_len}\t"
            f"warnings={warn_codes}\t"
            f"nat_reasons={','.join(self.naturalizer_reasons or []) or '-'}\t"
            f"nat_quality={self.nat_quality_score:.1f}\t"
            f"nat_mixed_pct={self.nat_mixed_language_pct:.1f}\t"
            f"nat_retry={esc(self.nat_retry_reason)!r}\t"
            f"nat_problems={','.join(self.nat_problems or []) or '-'}\t"
            f"nat_fixes={self.nat_fix_count}\t"
            f"nat_restored={','.join(self.nat_restored_entities or []) or '-'}\t"
            f"nat_warnings={','.join(self.nat_warnings or []) or '-'}\t"
            f"alt_mt={esc(self.alternative_translation)!r}\t"
            f"alt_route={esc(self.alternative_route)!r}\t"
            f"alt_engine={esc(self.alternative_engine)!r}\t"
            f"alt_score={self.alternative_score:.1f}\t"
            f"routes_tried={','.join(self.routes_tried or []) or '-'}\t"
            f"group={self.group_indices}"
        )


def synthesize_audits_from_segments(
    source_segments: list[str],
    translated_segments: list[str],
    src_lang: str,
    tgt_lang: str,
    *,
    engine: str = "cache",
) -> list[SegmentTranslationAudit]:
    """Build review audits when translate cache returns final text only."""
    from engines.translation_quality import run_quality_validation

    n = max(len(source_segments), len(translated_segments))
    texts = [
        str(translated_segments[i] if i < len(translated_segments) else "").strip()
        for i in range(n)
    ]
    _, validation_warnings = run_quality_validation(
        source_segments,
        texts,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        raw_segments=texts,
    )
    audits: list[SegmentTranslationAudit] = []
    for i in range(n):
        src = str(source_segments[i] if i < len(source_segments) else "")
        tr = texts[i]
        seg_warnings = validation_warnings[i] if i < len(validation_warnings) else []
        audits.append(
            SegmentTranslationAudit(
                index=i,
                source_lang=src_lang,
                target_lang=tgt_lang,
                whisper_text=src,
                raw_translation=tr,
                naturalized_text=tr,
                final_text=tr,
                tts_text=tr,
                quality_pass_before=tr,
                quality_pass_after=tr,
                semantic_text=tr,
                engine=engine,
                route="direct",
                validation_warnings=seg_warnings,
                whisper_len=len(src),
                raw_len=len(tr),
                naturalized_len=len(tr),
                final_len=len(tr),
            )
        )
    return audits


class TranslationQualityLog:
    LOG_NAME = "translation_quality.log"

    def __init__(self, app_dir: Path):
        self.log_dir = app_dir / "output" / "dev"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / self.LOG_NAME
        self._records: list[SegmentTranslationAudit] = []

    @property
    def path(self) -> str:
        return str(self.log_path)

    def add(self, record: SegmentTranslationAudit) -> None:
        self._records.append(record)

    def extend(self, records: list[SegmentTranslationAudit]) -> None:
        self._records.extend(records)

    def update_tts_texts(self, texts: list[str]) -> None:
        for rec in self._records:
            if rec.index < len(texts):
                rec.tts_text = str(texts[rec.index] or rec.final_text)

    def flush(
        self,
        *,
        task_id: str = "",
        extra: dict[str, Any] | None = None,
    ) -> str:
        if not self._records:
            return self.path

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        header = [f"=== task={task_id} ts={ts} segments={len(self._records)} ==="]
        if extra:
            for k, v in extra.items():
                header.append(f"{k}={v}")
        header.append(
            "idx\tsrc\ttgt\twhisper\traw_mt\tnaturalized\tquality_before\tquality_after\t"
            "semantic\tfinal\ttts\tengine\tmodel\troute\tpivot\tsemantic_adapted\t"
            "mt_ms\tnat_ms\tllm_ms\tquality_ms\tsemantic_ms\tw_len\traw_len\tnat_len\t"
            "final_len\twarnings\tgroup"
        )
        lines = header + [r.to_log_line() for r in sorted(self._records, key=lambda x: x.index)]

        with self.log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")

        return self.path

    def records_as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self._records]
