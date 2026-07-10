"""TubeDub Planner Agent v3.0 — READ ONLY pre-flight analysis and manifest builder."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.ai_core.capability_matrix import build_capability_matrix
from engines.ai_core.contracts import AgentExecutionResult, ProjectManifest
from engines.ai_core.gatekeeper import (
    default_agent_dependencies,
    default_fallback_map,
    default_success_criteria,
)
from engines.ai_core.smoke_tests import run_smoke_tests

logger = logging.getLogger("tubedub.ai_core.planner_agent")

_APP_DIR = Path(__file__).resolve().parents[2]
_OUTPUT_DIR = _APP_DIR / "output"
_MANIFESTS_DIR = _OUTPUT_DIR / "manifests"

# Average segment length for estimate (seconds).
_SEGMENT_EST_SEC = 8.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pipeline_version() -> str:
    try:
        from engines.app_version import APP_VERSION

        return str(APP_VERSION)
    except Exception:
        return "unknown"


def _ffprobe_json(video_path: str) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not Path(video_path).is_file():
        return {}
    try:
        res = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode != 0:
            return {}
        return json.loads(res.stdout or "{}")
    except Exception as exc:
        logger.debug("ffprobe failed: %s", exc)
        return {}


def _probe_video(video_path: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "exists": Path(video_path).is_file(),
        "audio_track_count": 0,
        "duration_ms": 0,
        "has_video": False,
    }
    if not info["exists"]:
        return info

    meta = _ffprobe_json(video_path)
    streams = meta.get("streams") or []
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    info["audio_track_count"] = len(audio_streams)
    info["has_video"] = len(video_streams) > 0

    fmt = meta.get("format") or {}
    try:
        dur_s = float(fmt.get("duration") or 0)
        info["duration_ms"] = int(dur_s * 1000)
    except (TypeError, ValueError):
        pass

    if not info["duration_ms"]:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            try:
                res = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "quiet",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        video_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                info["duration_ms"] = int(float(res.stdout.strip()) * 1000)
            except Exception:
                pass

    return info


def _language_hint(video_path: str, source_lang: str | None) -> tuple[str, float]:
    if source_lang:
        return source_lang, 0.95
    stem = Path(video_path).stem.lower()
    for code in ("en", "uk", "ru", "de", "fr", "es", "pl", "it"):
        if re.search(rf"\b{code}\b|_{code}_|_{code}$|^{code}_", stem):
            return code, 0.6
    try:
        from langdetect import detect

        sample = stem.replace("_", " ").replace("-", " ")
        if len(sample) >= 3:
            return detect(sample), 0.45
    except Exception:
        pass
    return "auto", 0.3


def _audio_heuristics(video_path: str, duration_ms: int) -> dict[str, Any]:
    """Cheap audio energy analysis via pydub (optional). READ ONLY — temp extract avoided."""
    result: dict[str, Any] = {
        "content_type": "mixed",
        "music_detected": False,
        "noise_level": "low",
        "audio_quality_score": 0.5,
        "speakers_estimate": 1,
    }
    if duration_ms <= 0 or not Path(video_path).is_file():
        return result

    try:
        from pydub import AudioSegment

        # Probe first 30s without writing to artifacts — ffmpeg pipe via pydub
        segment = AudioSegment.from_file(video_path)[:30_000]
        if len(segment) == 0:
            return result

        rms = float(segment.rms or 0)
        max_db = segment.max_dBFS if segment.max_dBFS != float("-inf") else -60.0
        silence_ratio = sum(1 for chunk in segment[::1000] if chunk.rms < 300) / max(
            1, len(segment) // 1000
        )

        # Heuristic buckets
        if rms < 200 and silence_ratio > 0.6:
            result["content_type"] = "speech"
            result["audio_quality_score"] = 0.35
            result["noise_level"] = "high"
        elif rms > 2500:
            result["content_type"] = "music"
            result["music_detected"] = True
            result["audio_quality_score"] = 0.75
        else:
            result["content_type"] = "mixed" if silence_ratio < 0.4 else "speech"
            result["audio_quality_score"] = min(1.0, 0.4 + rms / 5000.0)

        if max_db > -3:
            result["noise_level"] = "high"
        elif max_db > -12:
            result["noise_level"] = "medium"

        # Very dynamic range → possible multiple speakers / dialogue
        chunks = [c.rms for c in segment[::2000] if c.rms > 0]
        if chunks:
            dyn = (max(chunks) - min(chunks)) / max(max(chunks), 1)
            if dyn > 0.7 and result["content_type"] != "music":
                result["speakers_estimate"] = 2
    except Exception as exc:
        logger.debug("audio heuristics skipped: %s", exc)
        result["audio_quality_score"] = 0.4

    return result


def _segment_estimate(duration_ms: int) -> int:
    if duration_ms <= 0:
        return 0
    return max(1, int((duration_ms / 1000.0) / _SEGMENT_EST_SEC))


def _complexity_score(
    duration_ms: int,
    segment_est: int,
    audio_quality: float,
    content_type: str,
) -> str:
    score = 0
    minutes = duration_ms / 60_000.0
    if minutes > 45:
        score += 3
    elif minutes > 15:
        score += 2
    elif minutes > 5:
        score += 1

    if segment_est > 120:
        score += 2
    elif segment_est > 40:
        score += 1

    if audio_quality < 0.35:
        score += 2
    elif audio_quality < 0.55:
        score += 1

    if content_type == "music":
        score += 2
    elif content_type == "mixed":
        score += 1

    if score >= 6:
        return "EXTREME"
    if score >= 4:
        return "HIGH"
    if score >= 2:
        return "MEDIUM"
    return "LOW"


def _processing_strategy(
    complexity: str,
    cap: dict[str, Any],
    content_type: str,
) -> tuple[str, dict[str, str]]:
    reasons: dict[str, str] = {}
    if not cap.get("llm"):
        reasons["llm"] = "LLM unavailable — LOCAL_ONLY"
        return "LOCAL_ONLY", reasons

    if complexity in ("HIGH", "EXTREME"):
        if cap.get("gpu") and cap.get("llm"):
            reasons["quality"] = "High complexity + GPU → MAXIMUM_QUALITY"
            return "MAXIMUM_QUALITY", reasons
        reasons["quality"] = "High complexity without GPU → BALANCED"
        return "BALANCED", reasons

    if content_type == "music":
        reasons["content"] = "Music-heavy content → BALANCED"
        return "BALANCED", reasons

    if cap.get("llm") and not cap.get("gpu"):
        reasons["cloud"] = "LLM without local GPU → CLOUD_ASSISTED"
        return "CLOUD_ASSISTED", reasons

    if complexity == "LOW":
        reasons["speed"] = "Short/simple project → FAST"
        return "FAST", reasons

    reasons["default"] = "Default balanced path"
    return "BALANCED", reasons


def _resource_estimation(
    duration_ms: int,
    segment_est: int,
    strategy: str,
    cap: dict[str, Any],
) -> dict[str, Any]:
    minutes = max(0.1, duration_ms / 60_000.0)
    llm_calls = segment_est if strategy in ("BALANCED", "MAXIMUM_QUALITY", "CLOUD_ASSISTED") else max(
        1, segment_est // 4
    )
    tts_chars = int(segment_est * 80)
    base_sec = minutes * 90
    if strategy == "FAST":
        base_sec *= 0.7
    elif strategy == "MAXIMUM_QUALITY":
        base_sec *= 1.4

    gpu_mem = 2048 if cap.get("gpu") else 0
    ram_mb = 4096 if segment_est > 60 else 2048
    disk_mb = int(minutes * 25 + 100)

    return {
        "time_estimate_sec": int(base_sec),
        "llm_calls": llm_calls,
        "tts_chars": tts_chars,
        "gpu_mem_mb": gpu_mem,
        "ram_mb": ram_mb,
        "disk_mb": disk_mb,
    }


def _agent_capabilities(cap: dict[str, Any]) -> dict[str, dict[str, bool]]:
    return {
        "planner": {"read_only": True, "write_artifacts": True},
        "stt": {"enabled": cap.get("asr", False), "gpu": cap.get("gpu", False)},
        "translate": {"enabled": True, "llm": cap.get("llm", False)},
        "tts": {"enabled": cap.get("tts", False)},
        "dub": {"enabled": cap.get("ffmpeg", False)},
    }


def _confidence_scores(
    lang_hint: str,
    lang_conf: float,
    audio: dict[str, Any],
    cap: dict[str, Any],
) -> dict[str, float]:
    return {
        "language": round(lang_conf, 3),
        "content_type": 0.7 if audio.get("content_type") != "mixed" else 0.5,
        "voice_clone": 0.8 if cap.get("tts") else 0.2,
        "music": 0.85 if audio.get("music_detected") else 0.4,
        "speakers": 0.55 if audio.get("speakers_estimate", 1) > 1 else 0.7,
    }


class PlannerAgent:
    """READ ONLY planner — analyzes input video and emits manifest + report."""

    VERSION = "3.0"

    def __init__(self, output_dir: Path | None = None, *, app_dir: Path | None = None):
        self.app_dir = app_dir or _APP_DIR
        self.output_dir = output_dir or _OUTPUT_DIR
        self._manifests_dir = self.output_dir / "manifests"

    def run(
        self,
        video_path: str,
        target_lang: str,
        source_lang: str | None = None,
        task_id: str | None = None,
    ) -> AgentExecutionResult:
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []
        project_uuid = str(uuid.uuid4())

        video_probe = _probe_video(video_path)
        if not video_probe["exists"]:
            errors.append(f"video_not_found:{video_path}")

        lang_hint, lang_conf = _language_hint(video_path, source_lang)
        audio = _audio_heuristics(video_path, video_probe.get("duration_ms", 0))
        segment_est = _segment_estimate(video_probe.get("duration_ms", 0))

        decision_log.append(f"project_uuid={project_uuid}")
        decision_log.append(f"video_exists={video_probe['exists']}")

        cap = build_capability_matrix()
        smoke = run_smoke_tests(cap)

        if not cap.get("ffmpeg"):
            warnings.append("ffmpeg_missing")
        if not smoke.get("all_passed"):
            warnings.append("smoke_tests_incomplete")

        complexity = _complexity_score(
            video_probe.get("duration_ms", 0),
            segment_est,
            audio.get("audio_quality_score", 0.5),
            audio.get("content_type", "mixed"),
        )
        strategy, strategy_reasons = _processing_strategy(complexity, cap, audio.get("content_type", "mixed"))
        resources = _resource_estimation(
            video_probe.get("duration_ms", 0),
            segment_est,
            strategy,
            cap,
        )
        confidence = _confidence_scores(lang_hint, lang_conf, audio, cap)

        decision_reasons = {
            "complexity": f"duration+segments+quality → {complexity}",
            "strategy": strategy_reasons,
            "language": f"hint={lang_hint} conf={lang_conf}",
            "content_type": audio.get("content_type", "mixed"),
        }

        try:
            from engines.ai_core.platform.versions import MANIFEST_VERSION, platform_versions

            manifest_ver = MANIFEST_VERSION
            proto_versions = platform_versions()
        except Exception:
            manifest_ver = "3.0"
            proto_versions = {}

        manifest = ProjectManifest(
            project_uuid=project_uuid,
            pipeline_version=_pipeline_version(),
            manifest_version=manifest_ver,
            protocol_versions=proto_versions,
            task_id=task_id or "",
            video_path=str(video_path),
            target_lang=target_lang,
            source_lang=source_lang or lang_hint,
            created_at=_now_iso(),
            video_exists=bool(video_probe["exists"]),
            audio_track_count=int(video_probe.get("audio_track_count", 0)),
            duration_ms=int(video_probe.get("duration_ms", 0)),
            segment_count_estimate=segment_est,
            language_hint=lang_hint,
            content_type=audio.get("content_type", "mixed"),
            music_detected=bool(audio.get("music_detected")),
            noise_level=audio.get("noise_level", "low"),
            audio_quality_score=round(float(audio.get("audio_quality_score", 0.5)), 3),
            capability_matrix=cap,
            smoke_tests=smoke,
            confidence_scores=confidence,
            complexity_score=complexity,
            processing_strategy=strategy,
            resource_estimation=resources,
            decision_reasons=decision_reasons,
            agent_dependencies=default_agent_dependencies(),
            success_criteria=default_success_criteria(),
            fallback_map=default_fallback_map(),
            agent_capabilities=_agent_capabilities(cap),
        )

        manifest_dir = self._manifests_dir / project_uuid
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "project_manifest.json"
        report_path = manifest_dir / "planner_report.json"

        manifest_dict = manifest.to_dict()
        planner_report = {
            "project_uuid": project_uuid,
            "planner_version": self.VERSION,
            "task_id": task_id,
            "status": "error" if errors else ("warning" if warnings else "success"),
            "execution_time_ms": 0.0,
            "warnings": warnings,
            "errors": errors,
            "decision_log": decision_log,
            "smoke_summary": {
                "passed": smoke.get("passed"),
                "total": smoke.get("total"),
                "all_passed": smoke.get("all_passed"),
            },
            "manifest_path": str(manifest_path),
        }

        elapsed_ms = (time.perf_counter() - t0) * 1000
        planner_report["execution_time_ms"] = round(elapsed_ms, 1)

        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest_dict, fh, ensure_ascii=False, indent=2)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(planner_report, fh, ensure_ascii=False, indent=2)

        if task_id:
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "Planner/v3",
                    called=True,
                    success=not errors,
                    decision=strategy,
                    error="; ".join(errors) if errors else None,
                    fallback_used=bool(errors or warnings),
                )
                open_ddf.save(task_id)
            except Exception as exc:
                logger.debug("OpenDDF record failed: %s", exc)

        status = "error" if errors else ("warning" if warnings else "success")

        return AgentExecutionResult(
            status=status,
            updated_state={
                "project_uuid": project_uuid,
                "manifest_path": str(manifest_path),
                "planner_report_path": str(report_path),
                "manifest": manifest_dict,
                "planner_report": planner_report,
                "processing_strategy": strategy,
                "complexity_score": complexity,
            },
            metrics={
                "execution_time_ms": round(elapsed_ms, 1),
                "segment_count_estimate": segment_est,
                "duration_ms": video_probe.get("duration_ms", 0),
            },
            warnings=warnings,
            errors=errors,
            execution_time_ms=round(elapsed_ms, 1),
            decision_log=decision_log,
        )
