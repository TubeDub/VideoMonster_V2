"""Stage adapters — wrap existing engines without modifying them."""

from __future__ import annotations

import difflib
import re
from typing import Any

from engines.pipeline_platform.contract import (
    PipelineContext,
    StageDiagnostics,
    StageEnvelope,
    StageId,
    StageModule,
    StageStatus,
)
from engines.pipeline_platform.translation_optimizer_platform import optimize_translation_text
from engines.pipeline_platform.word_timing_bridge import (
    merge_word_timings_on_fewer_words,
    redistribute_word_timings,
    wtm_from_segment_info,
)


def _diff(before: str, after: str) -> list[dict[str, str]]:
    a = (before or "").split()
    b = (after or "").split()
    sm = difflib.SequenceMatcher(None, a, b)
    out: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        chunk = " ".join(b[j1:j2]) if tag != "delete" else " ".join(a[i1:i2])
        if chunk:
            out.append({"tag": tag, "text": chunk})
    return out


def _wtm_after_text_change(wtm: dict[str, Any], new_text: str) -> dict[str, Any]:
    words = list(wtm.get("words") or [])
    if not words:
        return dict(wtm)
    new_count = len(re.findall(r"\S+", new_text or ""))
    if new_count <= 0:
        return dict(wtm)
    word_dicts = [
        {
            "word": w.get("text") or w.get("word", ""),
            "start_ms": int(w.get("start_ms", 0)),
            "end_ms": int(w.get("end_ms", 0)),
            "confidence": float(w.get("confidence", 1.0)),
            "position": i,
        }
        for i, w in enumerate(words)
    ]
    if new_count < len(word_dicts):
        merged = merge_word_timings_on_fewer_words(word_dicts, new_count)
    elif new_count > len(word_dicts):
        merged = redistribute_word_timings(word_dicts, new_count)
    else:
        merged = word_dicts
    out = dict(wtm)
    out["words"] = [
        {
            "text": m.get("word", f"w{i}"),
            "start_ms": m["start_ms"],
            "end_ms": m["end_ms"],
            "confidence": m.get("confidence", 1.0),
            "position": m.get("position", i),
        }
        for i, m in enumerate(merged)
    ]
    return out


class _BaseAdapter(StageModule):
    def status(self) -> StageStatus:
        return StageStatus.OK


class SttStage(_BaseAdapter):
    stage_id = StageId.STT

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        original = ""
        if index < len(ctx.segments):
            original = ctx.segments[index]
        stt_text = original
        engine = "stt_engine"
        wtm = wtm_from_segment_info(ctx.info, index)
        audits = ctx.info.get("translation_audits") or []
        for a in audits:
            if int(a.get("index", -1)) == index and a.get("source_text"):
                stt_text = str(a["source_text"])
                break
        seg_data = (ctx.info.get("segments_data") or [])
        if index < len(seg_data) and seg_data[index].get("source_text"):
            stt_text = str(seg_data[index]["source_text"])
        diag = StageDiagnostics(engine=engine, quality_score=1.0 if stt_text else 0.0)
        diag.diff_from_previous = _diff(original, stt_text)
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=original,
            text_out=stt_text,
            status=StageStatus.OK.value if stt_text else StageStatus.WARNING.value,
            diagnostics=diag,
            word_timing_map=wtm,
        )


class TranslationManagerStage(_BaseAdapter):
    stage_id = StageId.TRANSLATION_MANAGER

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        text_in = envelope_in.text_out or envelope_in.text_in
        raw = text_in
        engine = "translation_manager"
        rules: list[str] = []
        audits = ctx.info.get("translation_audits") or []
        for a in audits:
            if int(a.get("index", -1)) != index:
                continue
            raw = str(a.get("raw_translation") or a.get("mt_text") or raw)
            engine = str(a.get("engine") or a.get("route") or engine)
            rules = list(a.get("rules_applied") or a.get("pipeline_stages") or [])
            break
        wtm = dict(envelope_in.word_timing_map)
        wtm = _wtm_after_text_change(wtm, raw)
        diag = StageDiagnostics(engine=engine, rules_applied=rules, quality_score=0.85)
        diag.diff_from_previous = _diff(text_in, raw)
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=text_in,
            text_out=raw,
            diagnostics=diag,
            word_timing_map=wtm,
        )


class EnterpriseTranslationStage(_BaseAdapter):
    stage_id = StageId.ENTERPRISE_TRANSLATION

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        text_in = envelope_in.text_out or envelope_in.text_in
        out = text_in
        engine = "enterprise_translation"
        status = StageStatus.SKIPPED.value
        audits = ctx.info.get("translation_audits") or []
        for a in audits:
            if int(a.get("index", -1)) != index:
                continue
            ent = a.get("enterprise_text") or a.get("enterprise_translation")
            if ent:
                out = str(ent)
                status = StageStatus.OK.value
                engine = str(a.get("enterprise_engine") or engine)
            break
        wtm = _wtm_after_text_change(dict(envelope_in.word_timing_map), out)
        diag = StageDiagnostics(engine=engine, quality_score=0.9 if status == StageStatus.OK.value else None)
        diag.diff_from_previous = _diff(text_in, out)
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=text_in,
            text_out=out,
            status=status,
            diagnostics=diag,
            word_timing_map=wtm,
        )


class NaturalTranslationStage(_BaseAdapter):
    stage_id = StageId.NATURAL_TRANSLATION

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        text_in = envelope_in.text_out or envelope_in.text_in
        out = text_in
        engine = "naturalizer"
        audits = ctx.info.get("translation_audits") or []
        for a in audits:
            if int(a.get("index", -1)) != index:
                continue
            nat = a.get("natural_text") or a.get("naturalized")
            if nat:
                out = str(nat)
            elif a.get("final_text"):
                out = str(a["final_text"])
            engine = str(a.get("naturalizer_engine") or engine)
            break
        wtm = _wtm_after_text_change(dict(envelope_in.word_timing_map), out)
        diag = StageDiagnostics(engine=engine, quality_score=0.88)
        diag.diff_from_previous = _diff(text_in, out)
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=text_in,
            text_out=out,
            diagnostics=diag,
            word_timing_map=wtm,
        )


class TranslationOptimizerStage(_BaseAdapter):
    stage_id = StageId.TRANSLATION_OPTIMIZER

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        text_in = envelope_in.text_out or envelope_in.text_in
        slot = ctx.segment_slot_ms(index)
        result = optimize_translation_text(
            text_in,
            slot_ms=slot,
            src_lang=ctx.src_lang,
            tgt_lang=ctx.tgt_lang,
        )
        out = result.optimized
        wtm = _wtm_after_text_change(dict(envelope_in.word_timing_map), out)
        diag = StageDiagnostics(
            engine="translation_optimizer_platform",
            rules_applied=["meaning_preserve", "grammar_check", "naturalness_check"],
            quality_score=float(result.quality_after.get("score", 0.8) or 0.8),
            warnings=list(result.warnings),
        )
        diag.diff_from_previous = _diff(text_in, out)
        diag.meta["steps"] = [s.to_dict() for s in result.steps]
        status = StageStatus.OK.value
        if "timing_error" in result.warnings:
            status = StageStatus.ERROR.value
        elif "timing_warning" in result.warnings:
            status = StageStatus.WARNING.value
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=text_in,
            text_out=out,
            status=status,
            diagnostics=diag,
            word_timing_map=wtm,
            artifacts={"optimizer": result.to_dict()},
        )


class TimingOptimizerStage(_BaseAdapter):
    stage_id = StageId.TIMING_OPTIMIZER

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        from engines.pipeline_platform.timing_engine import run_timing_engine

        text_in = envelope_in.text_out or envelope_in.text_in
        timing = run_timing_engine(
            text=text_in,
            slot_ms=ctx.segment_slot_ms(index),
            word_timing_map=dict(envelope_in.word_timing_map),
            src_lang=ctx.src_lang,
            tgt_lang=ctx.tgt_lang,
            allow_stretch=bool(ctx.info.get("style_allow_atempo", True)),
            max_stretch=float(ctx.info.get("style_max_atempo") or 1.18),
        )
        diag = StageDiagnostics(
            engine="timing_engine",
            duration_ms=timing.get("duration_ms", 0),
            quality_score=timing.get("quality_score"),
            warnings=list(timing.get("warnings") or []),
            errors=list(timing.get("errors") or []),
            rules_applied=list(timing.get("rules_applied") or []),
        )
        diag.meta = dict(timing)
        diag.diff_from_previous = _diff(text_in, timing.get("text_out", text_in))
        status = StageStatus.OK.value
        if timing.get("errors"):
            status = StageStatus.ERROR.value
        elif timing.get("warnings"):
            status = StageStatus.WARNING.value
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=text_in,
            text_out=str(timing.get("text_out") or text_in),
            status=status,
            diagnostics=diag,
            word_timing_map=dict(timing.get("word_timing_map") or envelope_in.word_timing_map),
            artifacts={"timing": timing},
        )


class TtsStage(_BaseAdapter):
    stage_id = StageId.TTS

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        text_in = envelope_in.text_out or envelope_in.text_in
        audio = ""
        engine = "tts"
        duration = 0
        seg_data = ctx.info.get("segments_data") or []
        if index < len(seg_data):
            row = seg_data[index]
            audio = str(row.get("file") or row.get("audio_path") or "")
            duration = int(row.get("duration_ms") or row.get("tts_ms") or 0)
            engine = str(row.get("tts_engine") or engine)
        diag = StageDiagnostics(engine=engine, duration_ms=duration)
        diag.diff_from_previous = _diff(text_in, text_in)
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=text_in,
            text_out=text_in,
            audio_path=audio,
            status=StageStatus.OK.value if audio else StageStatus.STUB.value,
            diagnostics=diag,
            word_timing_map=dict(envelope_in.word_timing_map),
        )


class AudioBuilderStage(_BaseAdapter):
    stage_id = StageId.AUDIO_BUILDER

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        audio = envelope_in.audio_path
        engine = "audio_builder"
        duration = envelope_in.diagnostics.duration_ms
        built = ctx.info.get("dub_audio_path") or ctx.info.get("built_audio_path") or ""
        diag = StageDiagnostics(engine=engine, duration_ms=duration)
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=envelope_in.text_out,
            text_out=envelope_in.text_out,
            audio_path=audio or str(built),
            diagnostics=diag,
            word_timing_map=dict(envelope_in.word_timing_map),
            status=StageStatus.OK.value if audio else StageStatus.STUB.value,
        )


class FinalMuxStage(_BaseAdapter):
    stage_id = StageId.FINAL_MUX

    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        mux_path = str(ctx.info.get("output_path_full") or ctx.info.get("output_file") or "")
        engine = "dub_engine"
        diag = StageDiagnostics(engine=engine)
        if index > 0:
            return StageEnvelope(
                stage_id=self.stage_id.value,
                segment_index=index,
                text_in=envelope_in.text_out,
                text_out=envelope_in.text_out,
                status=StageStatus.SKIPPED.value,
                diagnostics=diag,
                word_timing_map=dict(envelope_in.word_timing_map),
            )
        return StageEnvelope(
            stage_id=self.stage_id.value,
            segment_index=index,
            text_in=envelope_in.text_out,
            text_out=envelope_in.text_out,
            audio_path=mux_path,
            status=StageStatus.OK.value if mux_path else StageStatus.STUB.value,
            diagnostics=diag,
            word_timing_map=dict(envelope_in.word_timing_map),
            artifacts={"mux_path": mux_path},
        )
