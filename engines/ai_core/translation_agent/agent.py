"""TubeDub Translation Agent v1.0 — raw MT with multi-pass validation."""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.gatekeeper import (
    DependencyGate,
    SuccessGate,
    default_agent_dependencies,
    default_success_criteria,
)
from engines.ai_core.translation_agent.confidence import (
    SegmentConfidence,
    aggregate_confidence,
    translation_confidence,
)
from engines.ai_core.translation_agent.retry_policy import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    translate_with_fallback,
)
from engines.ai_core.translation_agent.translator_interface import TranslatorRegistry
from engines.ai_core.translation_agent.validators.entity_validator import validate_entities
from engines.ai_core.translation_agent.validators.language_validator import validate_language
from engines.ai_core.translation_agent.validators.terminology_validator import (
    build_glossary,
    validate_terminology,
)
from engines.mt.lang_codes import normalize_lang
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

logger = logging.getLogger("tubedub.ai_core.translation_agent")


def _brief(seg: dict) -> dict:
    raw = seg.get("creative_brief")
    return raw if isinstance(raw, dict) else {}

_APP_DIR = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _APP_DIR / "output"
_MANIFESTS_DIR = _OUTPUT_DIR / "manifests"


def _brief_translation_threshold(seg: dict[str, Any], default: float) -> float:
    """Raise confidence threshold when literal phrasing is important."""
    brief = seg.get("creative_brief") or {}
    if not brief:
        return default
    literal = float(brief.get("literal_phrasing_importance", 0.5))
    formality = float(brief.get("formality", 0.5))
    return min(0.98, default + (literal - 0.5) * 0.15 + (formality - 0.5) * 0.05)


def load_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Load project manifest JSON from planner output."""
    path = Path(manifest_path)
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


class TranslationAgent:
    """Raw MT only v4.0 — writes segments[].translated_text; no semantic adaptation."""

    VERSION = "4.0"

    def __init__(
        self,
        output_dir: Path | None = None,
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ):
        self.output_dir = output_dir or _OUTPUT_DIR
        self._manifests_dir = self.output_dir / "manifests"
        self.confidence_threshold = confidence_threshold

    def _check_gatekeeper(self, manifest: dict[str, Any]) -> tuple[bool, list[str]]:
        """Verify planner completed and translation dependencies are met."""
        warnings: list[str] = []
        if not manifest.get("project_uuid"):
            return False, ["planner_not_complete:missing_project_uuid"]
        if not manifest.get("planner_version"):
            warnings.append("planner_version_missing")

        cap = manifest.get("capability_matrix") or {}
        deps = manifest.get("agent_dependencies") or default_agent_dependencies()
        prereqs = deps.get("translate") or ["stt"]
        for prereq in prereqs:
            if prereq == "planner" and not manifest.get("project_uuid"):
                return False, ["planner_not_complete"]
            if prereq == "stt":
                continue  # assumed satisfied when segments are present

        gate = DependencyGate(cap)
        requirements = {"ffmpeg": False}  # translation does not require ffmpeg
        ok, missing = gate.check(requirements)
        if not ok:
            warnings.extend(missing)
        return True, warnings

    def _source_target(self, manifest: dict[str, Any]) -> tuple[str, str]:
        src = normalize_lang(manifest.get("source_lang") or "en")
        tgt = normalize_lang(manifest.get("target_lang") or "ru")
        return src, tgt

    def _detect_effective_source_lang(
        self,
        segments: list[dict],
        manifest_source: str,
    ) -> str:
        """Detect actual segment language when manifest source_lang is wrong."""
        from collections import Counter

        from engines.pipeline_language_gate import detect_segment_language

        declared = normalize_lang(manifest_source)
        counts: Counter[str] = Counter()
        for seg in segments[:8]:
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            lang = detect_segment_language(text, target_lang=declared)
            if lang not in ("empty", "unknown"):
                counts[lang] += 1
        if not counts:
            return declared
        detected, n = counts.most_common(1)[0]
        if detected != declared and n >= max(1, len(segments[:8]) // 3):
            return detected
        return declared

    def _pass1_translate(
        self,
        segments: list[dict],
        registry: TranslatorRegistry,
        source: str,
        target: str,
        decision_log: list[str],
        stats: dict[str, Any],
    ) -> None:
        """Pass 1: raw translation per segment."""
        for seg in segments:
            text = str(seg.get("text") or "").strip()
            if not text or source == target:
                seg["translated_text"] = text
                seg["confidence"] = {
                    "translation": 1.0,
                    "entity": 1.0,
                    "terminology": 1.0,
                    "language": 1.0,
                    "overall": 1.0,
                }
                continue

            result = translate_with_fallback(
                text,
                source,
                target,
                registry,
                threshold=_brief_translation_threshold(seg, self.confidence_threshold),
            )
            decision_log.extend(result.decision_log)
            if result.fallback_used:
                stats["fallback_used"] = True
            if result.attempt > 1:
                stats["retries"] += result.attempt - 1
            translated = str(result.translated or "").strip()
            if not translated or (
                source != target
                and translated == text
            ):
                lv = validate_language(
                    text,
                    translated or text,
                    source_lang=source,
                    target_lang=target,
                )
                if not lv.ok:
                    stats["warnings"].append(
                        f"segment_{seg.get('index', '?')}:pass1_language_fail {lv.issues}"
                    )
            if result.success and translated:
                stats["success_count"] += 1
            else:
                stats["warnings"].append(
                    f"segment_{seg.get('index', '?')}: {result.error}"
                )

            stats["translator_used"] = result.translator_name
            seg["translated_text"] = translated
            seg["translator_used"] = result.translator_name
            seg["translation_attempts"] = result.attempt
            conf = SegmentConfidence(translation=result.confidence)
            seg["confidence"] = {
                "translation": conf.translation,
                "entity": conf.entity,
                "terminology": conf.terminology,
                "language": conf.language,
                "overall": conf.overall,
            }

    def _pass2_entity(self, segments: list[dict], stats: dict[str, Any]) -> list[int]:
        """Pass 2: entity validation."""
        failed: list[int] = []
        entity_ok = 0
        for i, seg in enumerate(segments):
            ev = validate_entities(
                str(seg.get("text") or ""),
                str(seg.get("translated_text") or ""),
            )
            conf = seg.setdefault("confidence", {})
            conf["entity"] = ev.confidence
            conf["overall"] = SegmentConfidence(
                translation=float(conf.get("translation") or 0),
                entity=ev.confidence,
                terminology=float(conf.get("terminology") or 1),
                language=float(conf.get("language") or 1),
            ).overall
            if ev.ok:
                entity_ok += 1
            else:
                failed.append(i)
                stats["warnings"].append(
                    f"entity_fail segment={i} missing={ev.missing[:3]}"
                )
        stats["entity_validation"] = {
            "passed": entity_ok,
            "failed": len(failed),
            "total": len(segments),
        }
        return failed

    def _pass3_terminology(self, segments: list[dict], stats: dict[str, Any]) -> None:
        """Pass 3: terminology consistency across project."""
        glossary = build_glossary(segments)
        tv = validate_terminology(segments, glossary)
        stats["terminology_validation"] = {
            "ok": tv.ok,
            "confidence": tv.confidence,
            "inconsistent_terms": tv.inconsistent_terms[:10],
            "glossary_size": tv.glossary_size,
        }
        for seg in segments:
            conf = seg.setdefault("confidence", {})
            conf["terminology"] = tv.confidence
            conf["overall"] = SegmentConfidence(
                translation=float(conf.get("translation") or 0),
                entity=float(conf.get("entity") or 1),
                terminology=tv.confidence,
                language=float(conf.get("language") or 1),
            ).overall

    def _pass4_language(
        self,
        segments: list[dict],
        source: str,
        target: str,
        stats: dict[str, Any],
    ) -> list[int]:
        """Pass 4: language validation."""
        failed: list[int] = []
        lang_ok = 0
        for i, seg in enumerate(segments):
            lv = validate_language(
                str(seg.get("text") or ""),
                str(seg.get("translated_text") or ""),
                source_lang=source,
                target_lang=target,
            )
            conf = seg.setdefault("confidence", {})
            conf["language"] = lv.confidence
            conf["overall"] = SegmentConfidence(
                translation=float(conf.get("translation") or 0),
                entity=float(conf.get("entity") or 1),
                terminology=float(conf.get("terminology") or 1),
                language=lv.confidence,
            ).overall
            if lv.ok:
                lang_ok += 1
            else:
                failed.append(i)
                stats["warnings"].append(
                    f"language_fail segment={i} issues={lv.issues}"
                )
        stats["language_validation"] = {
            "passed": lang_ok,
            "failed": len(failed),
            "total": len(segments),
        }
        return failed

    def _pass5_fix(
        self,
        segments: list[dict],
        failed_indices: set[int],
        registry: TranslatorRegistry,
        source: str,
        target: str,
        decision_log: list[str],
        stats: dict[str, Any],
    ) -> None:
        """Pass 5: re-translate segments that failed validation."""
        if not failed_indices:
            return
        decision_log.append(f"pass5_retranslate count={len(failed_indices)}")
        for i in sorted(failed_indices):
            if i >= len(segments):
                continue
            seg = segments[i]
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            result = translate_with_fallback(
                text,
                source,
                target,
                registry,
                threshold=_brief_translation_threshold(seg, self.confidence_threshold),
            )
            decision_log.extend(result.decision_log)
            if result.success:
                seg["translated_text"] = result.translated
                stats["success_count"] += 1
            conf = seg.setdefault("confidence", {})
            conf["translation"] = result.confidence
            ev = validate_entities(text, str(seg.get("translated_text") or ""))
            lv = validate_language(
                text,
                str(seg.get("translated_text") or ""),
                source_lang=source,
                target_lang=target,
            )
            conf["entity"] = ev.confidence
            conf["language"] = lv.confidence
            conf["overall"] = SegmentConfidence(
                translation=result.confidence,
                entity=ev.confidence,
                terminology=float(conf.get("terminology") or 1),
                language=lv.confidence,
            ).overall

    def _save_report(
        self,
        manifest: dict[str, Any],
        task_id: str,
        stats: dict[str, Any],
        segments: list[dict],
        decision_log: list[str],
        elapsed_ms: float,
        status: str,
        errors: list[str],
        warnings: list[str],
    ) -> Path:
        project_uuid = manifest.get("project_uuid") or "unknown"
        report_dir = self._manifests_dir / project_uuid
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "translation_report.json"

        agg = aggregate_confidence(segments)
        per_segment = []
        for seg in segments:
            conf = seg.get("confidence") or {}
            per_segment.append(
                {
                    "index": seg.get("index"),
                    "confidence": conf,
                    "translator_used": seg.get("translator_used"),
                }
            )

        report = {
            "translation_agent_version": self.VERSION,
            "project_uuid": project_uuid,
            "task_id": task_id,
            "status": status,
            "translator_used": stats.get("translator_used"),
            "segment_count": stats.get("segment_count", 0),
            "success_count": stats.get("success_count", 0),
            "retries": stats.get("retries", 0),
            "fallback_used": stats.get("fallback_used", False),
            "warnings": warnings + stats.get("warnings", []),
            "errors": errors,
            "execution_time_ms": round(elapsed_ms, 1),
            "avg_confidence": agg,
            "entity_validation": stats.get("entity_validation"),
            "terminology_validation": stats.get("terminology_validation"),
            "language_validation": stats.get("language_validation"),
            "decision_log": decision_log,
            "per_segment_confidence": per_segment,
        }

        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

        return report_path

    def run(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> AgentExecutionResult:
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []

        try:
            return self._run_impl(manifest, state, task_id, t0, warnings, errors, decision_log)
        except Exception as exc:
            debug_mode = IS_DEBUG_LEARNING_MODE()
            logger.exception("Translation agent failed: %s", exc)
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "Translation/v1",
                    called=True,
                    success=False,
                    error=str(exc),
                    decision="LLM skipped",
                    fallback_used=True,
                )
                open_ddf.save(task_id)
            except Exception as ddf_exc:
                logger.debug("OpenDDF record failed: %s", ddf_exc)
            elapsed = (time.perf_counter() - t0) * 1000
            if debug_mode:
                segments = copy.deepcopy(state.get("segments") or [])
                for seg in segments:
                    if not str(seg.get("translated_text") or "").strip():
                        seg["translated_text"] = str(seg.get("text") or "")
                return AgentExecutionResult(
                    status="warning",
                    updated_state={"segments": segments},
                    metrics={"execution_time_ms": round(elapsed, 1), "debug_mode": True},
                    warnings=warnings + [str(exc)],
                    errors=[],
                    execution_time_ms=round(elapsed, 1),
                    decision_log=decision_log,
                )
            raise

    def _run_impl(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
        t0: float,
        warnings: list[str],
        errors: list[str],
        decision_log: list[str],
    ) -> AgentExecutionResult:
        gate_ok, gate_msgs = self._check_gatekeeper(manifest)
        if not gate_ok:
            errors.extend(gate_msgs)
            elapsed = (time.perf_counter() - t0) * 1000
            debug_mode = IS_DEBUG_LEARNING_MODE()
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "Translation/v1",
                    called=True,
                    success=False,
                    error="; ".join(gate_msgs),
                    fallback_used=debug_mode,
                )
                open_ddf.save(task_id)
            except Exception as exc:
                logger.debug("OpenDDF record failed: %s", exc)
            if debug_mode:
                segments = copy.deepcopy(state.get("segments") or [])
                for seg in segments:
                    if not str(seg.get("translated_text") or "").strip():
                        seg["translated_text"] = str(seg.get("text") or "")
                return AgentExecutionResult(
                    status="warning",
                    updated_state={"segments": segments},
                    metrics={"execution_time_ms": round(elapsed, 1), "debug_mode": True},
                    warnings=warnings + gate_msgs,
                    errors=[],
                    execution_time_ms=round(elapsed, 1),
                    decision_log=decision_log,
                )
            return AgentExecutionResult(
                status="error",
                updated_state={"segments": state.get("segments") or []},
                metrics={"execution_time_ms": round(elapsed, 1)},
                warnings=warnings,
                errors=errors,
                execution_time_ms=round(elapsed, 1),
                decision_log=decision_log,
            )
        warnings.extend(gate_msgs)

        segments_in = state.get("segments") or []
        segments = copy.deepcopy(segments_in)
        segment_count = len(segments)
        decision_log.append(f"segment_count={segment_count}")

        source, target = self._source_target(manifest)
        effective_source = self._detect_effective_source_lang(segments, source)
        if effective_source != source:
            decision_log.append(f"source_lang_corrected {source}->{effective_source}")
            source = effective_source
        decision_log.append(f"lang={source}->{target}")

        cap = manifest.get("capability_matrix") or {}
        registry = TranslatorRegistry(cap)

        stats: dict[str, Any] = {
            "segment_count": segment_count,
            "success_count": 0,
            "retries": 0,
            "fallback_used": False,
            "warnings": [],
            "translator_used": None,
        }

        # Pass 1: Raw translation
        self._pass1_translate(segments, registry, source, target, decision_log, stats)

        # Pass 2: Entity validation
        entity_failed = self._pass2_entity(segments, stats)

        # Pass 3: Terminology validation
        self._pass3_terminology(segments, stats)

        # Pass 4: Language validation
        lang_failed = self._pass4_language(segments, source, target, stats)

        # Pass 5: Fix failed validations
        failed_set = set(entity_failed) | set(lang_failed)
        self._pass5_fix(
            segments, failed_set, registry, source, target, decision_log, stats
        )

        # Pass 6: Translation completeness guarantee (no empty segments)
        try:
            from engines.dub_quality_stabilization import guarantee_translation_completeness

            fixed_n, completeness_rows = guarantee_translation_completeness(
                segments,
                source_lang=source,
                target_lang=target,
                registry=registry,
                task_id=task_id,
            )
            stats["completeness_fixed"] = fixed_n
            stats["completeness_rows"] = completeness_rows
            empty_left = sum(1 for r in completeness_rows if r.get("status") == "failed")
            if empty_left:
                warnings.append(f"translation_incomplete_segments={empty_left}")
                decision_log.append(f"pass6_incomplete count={empty_left}")
            else:
                decision_log.append(f"pass6_completeness ok fixed={fixed_n}")
        except Exception as exc:
            logger.warning("Translation completeness pass failed: %s", exc)
            warnings.append(f"completeness_pass_error:{exc}")

        # Integrity: never mutate timing/speaker/order — only translated_text
        for i, (orig, out) in enumerate(zip(segments_in, segments)):
            for key in ("start", "end", "speaker", "text", "index"):
                if key in orig:
                    out[key] = orig[key]
            out.setdefault("index", i)

        agg = aggregate_confidence(segments)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        success_criteria = manifest.get("success_criteria") or default_success_criteria()
        translate_criteria = success_criteria.get("translate") or {"segments_min": 1}
        metrics_payload = {
            "segments_min": sum(
                1 for s in segments if str(s.get("translated_text") or "").strip()
            ),
            "segment_count": segment_count,
            "success_count": stats["success_count"],
            "retries": stats["retries"],
            **agg,
        }
        sg = SuccessGate()
        success_ok, sg_failures = sg.evaluate(translate_criteria, metrics_payload)
        if not success_ok:
            warnings.extend([f"success_gate:{f}" for f in sg_failures])

        status = "error" if errors else ("warning" if warnings else "success")
        report_path = self._save_report(
            manifest,
            task_id,
            stats,
            segments,
            decision_log,
            elapsed_ms,
            status,
            errors,
            warnings,
        )

        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "Translation/v1",
                called=True,
                success=status != "error",
                decision=(
                    "llm"
                    if stats.get("translator_used") and not stats.get("fallback_used")
                    else ("LLM skipped" if stats.get("fallback_used") else str(stats.get("translator_used") or "rule"))
                ),
                error="; ".join(errors) if errors else None,
                fallback_used=bool(stats.get("fallback_used")),
            )
            open_ddf.save(task_id)
        except Exception as exc:
            logger.debug("OpenDDF record failed: %s", exc)

        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "translation_report_path": str(report_path),
                "project_uuid": manifest.get("project_uuid"),
            },
            metrics={
                "execution_time_ms": round(elapsed_ms, 1),
                "segment_count": segment_count,
                "success_count": stats["success_count"],
                "retries": stats["retries"],
                "fallback_used": stats["fallback_used"],
                "translator_used": stats.get("translator_used"),
                "per_segment_confidence": [
                    s.get("confidence") for s in segments
                ],
                **agg,
            },
            warnings=warnings + stats.get("warnings", []),
            errors=errors,
            execution_time_ms=round(elapsed_ms, 1),
            decision_log=decision_log,
        )
