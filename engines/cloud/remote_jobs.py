"""Future remote processing queue (translate, whisper, TTS, render, dub)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from engines.cloud.models import RemoteJobKind, RemoteJobTarget


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
    error: str = ""

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
            "error": self.error,
        }


class RemoteJobQueue:
    """Stub queue — cloud server integration in Stage 2."""

    SUPPORTED = [k.value for k in RemoteJobKind]

    def __init__(self):
        self._jobs: dict[str, RemoteJob] = {}

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
            raise NotImplementedError(
                "Remote cloud execution pending TubeDub Cloud server (Stage 2). "
                "Use target=local for now."
            )
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
        job.state = "local_only"
        job.error = "Cloud remote jobs not yet connected"
        self._jobs[job.job_id] = job
        return job

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = sorted(self._jobs.values(), key=lambda j: j.created_ms, reverse=True)
        return [j.to_dict() for j in rows[:limit]]

    def get(self, job_id: str) -> RemoteJob | None:
        return self._jobs.get(job_id)
