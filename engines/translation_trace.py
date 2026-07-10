"""Single-run translation pipeline trace — output/dev/translation_trace.log"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.translation_quality import segment_quality_warnings, validate_raw_mt


@dataclass
class SegmentTrace:
    index: int
    whisper: str = ""
    raw_mt: str = ""
    naturalized: str = ""
    quality_before: str = ""
    quality_after: str = ""
    semantic: str = ""
    final: str = ""
    tts: str = ""
    engine: str = ""
    route: str = ""
    source_lang: str = ""
    target_lang: str = ""
    mt_ms: float = 0.0
    naturalizer_ms: float = 0.0
    llm_ms: float = 0.0
    quality_ms: float = 0.0
    semantic_ms: float = 0.0
    whisper_len: int = 0
    raw_len: int = 0
    naturalized_len: int = 0
    final_len: int = 0
    validation_warnings: list[dict[str, Any]] = field(default_factory=list)
    naturalizer_reasons: list[str] = field(default_factory=list)
    nat_quality_score: float = 0.0
    nat_mixed_language_pct: float = 0.0
    nat_retry_reason: str = ""
    nat_problems: list[str] = field(default_factory=list)
    nat_fix_count: int = 0
    nat_restored_entities: list[str] = field(default_factory=list)
    nat_warnings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def collect_issues(self) -> list[str]:
        out: list[str] = list(self.issues)
        out.extend(
            validate_raw_mt(
                self.whisper,
                self.raw_mt,
                source_lang=self.source_lang,
                target_lang=self.target_lang,
            )
        )
        if self.naturalized and self.final and self.naturalized != self.final:
            out.append("semantic_adapted")
        for w in segment_quality_warnings(
            original=self.whisper,
            raw=self.raw_mt,
            naturalized=self.naturalized,
            final=self.final,
            tts_text=self.tts or self.final,
            source_lang=self.source_lang,
            target_lang=self.target_lang,
        ):
            code = w.get("code", "")
            stage = w.get("stage", "")
            out.append(f"{stage}:{code}" if stage else code)
        return sorted(set(out))

    def to_log_line(self) -> str:
        def esc(s: str) -> str:
            return (s or "").replace("\t", " ").replace("\n", " ").strip()[:400]

        issues = self.collect_issues()
        for w in self.validation_warnings:
            code = w.get("code", "")
            stage = w.get("stage", "")
            label = f"{stage}:{code}" if stage else str(code)
            if label and label not in issues:
                issues.append(label)
        for r in self.naturalizer_reasons or []:
            label = f"nat:{r}"
            if label not in issues:
                issues.append(label)
        if self.nat_retry_reason:
            issues.append(f"nat_retry:{self.nat_retry_reason}")
        if self.nat_mixed_language_pct > 0:
            issues.append(f"nat_mixed:{self.nat_mixed_language_pct:.1f}%")
        for p in self.nat_problems or []:
            label = f"nat_problem:{p}"
            if label not in issues:
                issues.append(label)
        if self.nat_fix_count:
            issues.append(f"nat_fixes:{self.nat_fix_count}")
        if self.nat_quality_score:
            issues.append(f"nat_quality:{self.nat_quality_score:.1f}")
        for e in self.nat_restored_entities or []:
            label = f"nat_restored:{e}"
            if label not in issues:
                issues.append(label)
        for w in self.nat_warnings or []:
            label = f"nat_warn:{w}"
            if label not in issues:
                issues.append(label)
        issues = sorted(set(issues))
        return (
            f"idx={self.index}\t"
            f"src={self.source_lang}\t"
            f"tgt={self.target_lang}\t"
            f"whisper={esc(self.whisper)!r}\t"
            f"raw_mt={esc(self.raw_mt)!r}\t"
            f"naturalized={esc(self.naturalized)!r}\t"
            f"quality_before={esc(self.quality_before)!r}\t"
            f"quality_after={esc(self.quality_after)!r}\t"
            f"semantic={esc(self.semantic)!r}\t"
            f"final={esc(self.final)!r}\t"
            f"tts={esc(self.tts or self.final)!r}\t"
            f"engine={self.engine}\t"
            f"route={self.route}\t"
            f"mt_ms={self.mt_ms:.1f}\t"
            f"nat_ms={self.naturalizer_ms:.1f}\t"
            f"llm_ms={self.llm_ms:.1f}\t"
            f"quality_ms={self.quality_ms:.1f}\t"
            f"semantic_ms={self.semantic_ms:.1f}\t"
            f"w_len={self.whisper_len}\t"
            f"raw_len={self.raw_len}\t"
            f"nat_len={self.naturalized_len}\t"
            f"final_len={self.final_len}\t"
            f"issues={','.join(issues) if issues else '-'}"
        )


class TranslationTraceLog:
    LOG_NAME = "translation_trace.log"

    def __init__(self, app_dir: Path, task_id: str = ""):
        self.app_dir = app_dir
        self.task_id = task_id
        self.log_dir = app_dir / "output" / "dev"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / self.LOG_NAME
        self._traces: dict[int, SegmentTrace] = {}

    @property
    def path(self) -> str:
        return str(self.log_path)

    def upsert_from_audit(self, audit: dict[str, Any]) -> None:
        idx = int(audit.get("index", -1))
        if idx < 0:
            return
        tr = self._traces.get(idx) or SegmentTrace(index=idx)
        tr.whisper = str(audit.get("whisper_text") or tr.whisper)
        tr.raw_mt = str(audit.get("raw_translation") or tr.raw_mt)
        tr.naturalized = str(audit.get("naturalized_text") or tr.naturalized)
        tr.quality_before = str(audit.get("quality_pass_before") or tr.quality_before)
        tr.quality_after = str(audit.get("quality_pass_after") or tr.quality_after)
        tr.semantic = str(audit.get("semantic_text") or tr.semantic)
        tr.final = str(audit.get("final_text") or tr.final)
        tr.tts = str(audit.get("tts_text") or tr.tts)
        tr.engine = str(audit.get("engine") or tr.engine)
        tr.route = str(audit.get("route") or tr.route)
        tr.source_lang = str(audit.get("source_lang") or tr.source_lang)
        tr.target_lang = str(audit.get("target_lang") or tr.target_lang)
        tr.mt_ms = float(audit.get("duration_ms") or audit.get("mt_ms") or tr.mt_ms)
        tr.naturalizer_ms = float(audit.get("naturalizer_ms") or tr.naturalizer_ms)
        tr.llm_ms = float(audit.get("llm_ms") or tr.llm_ms)
        tr.quality_ms = float(audit.get("quality_pass_ms") or tr.quality_ms)
        tr.semantic_ms = float(audit.get("semantic_ms") or tr.semantic_ms)
        tr.whisper_len = int(audit.get("whisper_len") or tr.whisper_len or len(tr.whisper))
        tr.raw_len = int(audit.get("raw_len") or tr.raw_len or len(tr.raw_mt))
        tr.naturalized_len = int(audit.get("naturalized_len") or tr.naturalized_len or len(tr.naturalized))
        tr.final_len = int(audit.get("final_len") or tr.final_len or len(tr.final))
        vw = audit.get("validation_warnings")
        if vw:
            tr.validation_warnings = list(vw)
        nr = audit.get("naturalizer_reasons")
        if nr:
            tr.naturalizer_reasons = list(nr)
        tr.nat_quality_score = float(audit.get("nat_quality_score") or tr.nat_quality_score)
        tr.nat_mixed_language_pct = float(
            audit.get("nat_mixed_language_pct") or tr.nat_mixed_language_pct
        )
        tr.nat_retry_reason = str(audit.get("nat_retry_reason") or tr.nat_retry_reason)
        np_ = audit.get("nat_problems")
        if np_:
            tr.nat_problems = list(np_)
        tr.nat_fix_count = int(audit.get("nat_fix_count") or tr.nat_fix_count)
        nre = audit.get("nat_restored_entities")
        if nre:
            tr.nat_restored_entities = list(nre)
        nw = audit.get("nat_warnings")
        if nw:
            tr.nat_warnings = list(nw)
        self._traces[idx] = tr

    def sync_audits(
        self,
        audits: list[dict[str, Any]],
        segments_data: list[dict],
        *,
        prosody_only: bool = False,
    ) -> None:
        sync_translation_audits(
            audits, segments_data, trace=self, prosody_only=prosody_only
        )

    def flush(self, *, phase: str = "complete", extra: dict[str, Any] | None = None) -> str:
        if not self._traces:
            return self.path
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        header = [f"=== task={self.task_id} phase={phase} ts={ts} ==="]
        if extra:
            for k, v in extra.items():
                header.append(f"{k}={v}")
        header.append(
            "idx\tsrc\ttgt\twhisper\traw_mt\tnaturalized\tquality_before\tquality_after\t"
            "semantic\tfinal\ttts\tengine\troute\tmt_ms\tnat_ms\tllm_ms\tquality_ms\t"
            "semantic_ms\tw_len\traw_len\tnat_len\tfinal_len\tissues"
        )
        lines = header + [
            self._traces[i].to_log_line()
            for i in sorted(self._traces)
        ]
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
        return self.path


def _is_ssml_text(text: str) -> bool:
    return str(text or "").lstrip().startswith("<speak")


def _segment_plain_and_tts(seg: dict[str, Any]) -> tuple[str, str]:
    plain = str(
        seg.get("plain_text") or seg.get("translation_text") or ""
    ).strip()
    tts = str(seg.get("tts_text") or seg.get("text") or "").strip()
    if not plain and tts and not _is_ssml_text(tts):
        plain = tts
    if _is_ssml_text(tts) and not plain:
        plain = str(seg.get("text") or "").strip()
        if _is_ssml_text(plain):
            plain = ""
    return plain, tts


def sync_translation_audits(
    audits: list[dict[str, Any]],
    segments_data: list[dict],
    *,
    trace: TranslationTraceLog | None = None,
    prosody_only: bool = False,
) -> None:
    """Update tts_text after TTS prep; Final stays plain unless semantic adapt ran."""
    audit_by_idx = {int(a.get("index", -1)): a for a in audits}
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        row = audit_by_idx.get(idx)
        if not row:
            continue
        plain, tts = _segment_plain_and_tts(seg)
        if not plain and not tts:
            continue

        if tts:
            row["tts_text"] = tts

        if prosody_only or _is_ssml_text(tts):
            if plain and not _is_ssml_text(plain):
                row.setdefault("final_text", plain)
            if trace:
                trace.upsert_from_audit(row)
            continue

        nat = str(row.get("naturalized_text") or "")
        use = plain or tts
        row["semantic_text"] = use
        row["final_text"] = use
        row["tts_text"] = use
        if nat and use != nat:
            row["semantic_adapted"] = True
        if trace:
            trace.upsert_from_audit(row)
