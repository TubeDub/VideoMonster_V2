"""Local + cloud remote processing queue (translate, whisper, TTS, render, dub)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.cloud.models import RemoteJobKind, RemoteJobTarget

logger = logging.getLogger("tubedub.cloud.remote_jobs")

CLOUD_TARGET_MSG_EN = (
    "Remote cloud execution is not available: TubeDub Cloud server is not configured "
    "(set VM_TUBEDUB_CLOUD_URL + credentials). Use target=local for now."
)
CLOUD_TARGET_MSG_RU = (
    "Удалённое облачное выполнение недоступно: сервер TubeDub Cloud не настроен "
    "(нужны VM_TUBEDUB_CLOUD_URL и учётные данные). Сейчас используйте target=local."
)


class CloudTargetUnavailableError(NotImplementedError):
    """Honest hard-gate — never fake success for target=cloud without a server."""

    def __init__(self, message: str | None = None):
        super().__init__(message or CLOUD_TARGET_MSG_EN)
        self.message_en = CLOUD_TARGET_MSG_EN
        self.message_ru = CLOUD_TARGET_MSG_RU


@dataclass
class RemoteJob:
    job_id: str
    kind: str
    target: str = RemoteJobTarget.LOCAL.value
    project_id: str = ""
    provider_id: str = "tubedub_cloud"
    payload: dict[str, Any] = field(default_factory=dict)
    state: str = "queued"
    created_ms: int = 0
    finished_ms: int = 0
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "target": self.target,
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "payload": self.payload,
            "state": self.state,
            "created_ms": self.created_ms,
            "finished_ms": self.finished_ms,
            "error": self.error,
            "result": self.result,
        }


class RemoteJobQueue:
    """Executes local jobs immediately; cloud target needs TubeDub Cloud server."""

    SUPPORTED = [k.value for k in RemoteJobKind]

    def __init__(self, app_dir: Path | None = None):
        self._jobs: dict[str, RemoteJob] = {}
        self._lock = threading.RLock()
        self.app_dir = Path(app_dir) if app_dir else Path(__file__).resolve().parents[2]

    def submit(
        self,
        kind: str,
        *,
        target: str = RemoteJobTarget.LOCAL.value,
        project_id: str = "",
        provider_id: str = "tubedub_cloud",
        payload: dict | None = None,
    ) -> RemoteJob:
        if kind not in self.SUPPORTED:
            raise ValueError(f"Unsupported job kind: {kind}")
        if target == RemoteJobTarget.CLOUD.value:
            raise CloudTargetUnavailableError()
        job = RemoteJob(
            job_id=str(uuid.uuid4()),
            kind=kind,
            target=target,
            project_id=project_id,
            provider_id=provider_id,
            payload=payload or {},
            state="queued",
            created_ms=int(time.time() * 1000),
        )
        with self._lock:
            self._jobs[job.job_id] = job
        t = threading.Thread(
            target=self._run_local,
            args=(job.job_id,),
            daemon=True,
            name=f"remote-job-{job.kind}-{job.job_id[:8]}",
        )
        t.start()
        return job

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for k, v in fields.items():
                setattr(job, k, v)

    def _run_local(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            kind = job.kind
            payload = dict(job.payload or {})
        self._update(job_id, state="running")
        try:
            result = self._execute(kind, payload)
            self._update(
                job_id,
                state="completed",
                result=result,
                finished_ms=int(time.time() * 1000),
                error="",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("remote job %s failed", job_id)
            self._update(
                job_id,
                state="error",
                error=str(exc)[:500],
                finished_ms=int(time.time() * 1000),
            )

    def _safe_media_path(self, raw: str) -> Path:
        """Resolve media only under uploads/output (block arbitrary FS reads)."""
        from engines.path_safety import resolve_under_roots

        text = str(raw or "").strip()
        if not text:
            raise ValueError("path required")
        roots = [
            self.app_dir / "uploads",
            self.app_dir / "output",
            self.app_dir / "projects",
        ]
        hit = resolve_under_roots(text, roots, basename_fallback=True)
        if hit is None or not hit.is_file():
            raise FileNotFoundError(text)
        return hit

    def _safe_output_path(self, raw: str, *, default_name: str) -> Path:
        from engines.path_safety import clamp_write_path, safe_filename

        dest_dir = self.app_dir / "output" / "remote_jobs"
        name = safe_filename(Path(str(raw or default_name)).name, default=Path(default_name).stem)
        suffix = Path(str(raw or default_name)).suffix or Path(default_name).suffix
        return clamp_write_path(f"{name}{suffix}", dest_dir, default_name=default_name)

    def _execute(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind == RemoteJobKind.TRANSLATE.value:
            text = str(payload.get("text") or "").strip()
            if not text:
                raise ValueError("payload.text required")
            src = str(payload.get("src_lang") or payload.get("source") or "en")
            tgt = str(payload.get("tgt_lang") or payload.get("target") or "ru")
            from engines.translation import translate_text

            out = translate_text(text, src, tgt)
            return {"text": out, "src_lang": src, "tgt_lang": tgt}

        if kind == RemoteJobKind.WHISPER.value:
            audio = str(payload.get("audio_path") or payload.get("path") or "").strip()
            if not audio:
                raise ValueError("payload.audio_path required")
            path = self._safe_media_path(audio)
            from engines.stt_engine import transcribe

            model = str(payload.get("model") or "tiny")
            lang = payload.get("language")
            text, srt, timing, detected = transcribe(
                str(path), language=lang, model_size=model
            )
            return {
                "text": text,
                "srt": srt,
                "timing_map": timing,
                "detected_lang": detected,
            }

        if kind == RemoteJobKind.TTS.value:
            text = str(payload.get("text") or "").strip()
            if not text:
                raise ValueError("payload.text required")
            voice = str(payload.get("voice") or "ru-RU-DmitryNeural")
            out_name = str(payload.get("output") or f"remote_tts_{uuid.uuid4().hex[:8]}.mp3")
            out_path = self._safe_output_path(out_name, default_name="remote_tts.mp3")
            from engines.tts_engines.registry import synthesize

            res = synthesize(
                text,
                voice,
                str(out_path),
                engine_id=payload.get("engine_id"),
                rate=payload.get("rate"),
                pitch=payload.get("pitch"),
            )
            if not res.ok:
                raise RuntimeError(res.error or "tts_failed")
            return {"output_path": res.output_path, "engine_id": res.engine_id}

        if kind == RemoteJobKind.AUDIO.value:
            src = str(payload.get("audio_path") or payload.get("path") or "").strip()
            if not src:
                raise ValueError("payload.audio_path required")
            path = self._safe_media_path(src)
            from engines.plugins.registry import process_chain

            order = payload.get("plugins") or None
            out = process_chain(path, self.app_dir, order=order)
            return {"output_path": out}

        if kind in (RemoteJobKind.RENDER.value, RemoteJobKind.DUB.value):
            # Local mirror of cloud render: require an existing output video path
            # or kick a lightweight ffmpeg remux when video+audio provided.
            video = str(payload.get("video_path") or "").strip()
            audio = str(payload.get("audio_path") or "").strip()
            if video and audio:
                vpath = self._safe_media_path(video)
                apath = self._safe_media_path(audio)
                out = self._safe_output_path(
                    f"dub_{uuid.uuid4().hex[:8]}.mp4", default_name="dub.mp4"
                )
                from engines.ffmpeg_paths import find_ffmpeg
                import subprocess

                ffmpeg = find_ffmpeg()
                if not ffmpeg:
                    raise RuntimeError("ffmpeg not found")
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-i",
                        str(vpath),
                        "-i",
                        str(apath),
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-shortest",
                        str(out),
                    ],
                    check=True,
                    capture_output=True,
                )
                return {"output_path": str(out)}
            raise ValueError(
                f"{kind} requires payload.video_path + payload.audio_path "
                "(full auto-dub remains on /api/auto_dub/start)"
            )

        raise ValueError(f"Unhandled kind: {kind}")

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self._jobs.values(), key=lambda j: j.created_ms, reverse=True)
            return [j.to_dict() for j in rows[:limit]]

    def get(self, job_id: str) -> RemoteJob | None:
        with self._lock:
            return self._jobs.get(job_id)
