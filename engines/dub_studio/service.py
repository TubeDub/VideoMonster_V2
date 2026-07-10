"""Dub Studio orchestrator."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from engines.dub_studio.config import studio_config
from engines.dub_studio.emotion import apply_emotion_bridge, emotion_to_tts_params, extract_emotion
from engines.dub_studio.fx.chain import FxChain, FxPipeline, FxSlotSpec
from engines.dub_studio.fx.registry import list_plugins
from engines.dub_studio.import_dub import import_from_review_json
from engines.dub_studio.models import DubProject, FxSlot, SegmentVersion, StudioSegment, StudioTrack
from engines.dub_studio.recording import RecordingManager
from engines.dub_studio.store import ProjectStore
from engines.dub_studio.export import export_timeline_wav
from engines.dub_studio.tts_regen import regenerate_segment_audio
from engines.dub_studio.timing import time_stretch_audio, update_segment_timing

_SERVICES: dict[str, "DubStudioService"] = {}
_LOCK = threading.RLock()
_PREVIEW_BUFFERS: dict[str, bytes] = {}


class DubStudioService:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self.store = ProjectStore(self.app_dir)
        self.recording = RecordingManager(self.app_dir)
        cfg = studio_config()
        self.fx_pipeline = FxPipeline(max_workers=cfg["worker_threads"])
        self._stretch_max = cfg["time_stretch_max"]

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "plugins": list_plugins(),
            "projects": len(self.store.list_projects()),
            "config": studio_config(),
        }

    def list_projects(self) -> list[dict[str, Any]]:
        return self.store.list_projects()

    def create_project(self, *, title: str = "Dub Studio Project") -> DubProject:
        return self.store.create_empty(title=title)

    def get_project(self, project_id: str) -> DubProject | None:
        return self.store.load(project_id)

    def import_review(self, review_path: Path, *, title: str = "") -> DubProject:
        return import_from_review_json(self.app_dir, review_path, title=title)

    def set_track_solo(self, project_id: str, track_id: str, solo: bool) -> DubProject:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        for t in project.tracks:
            if t.track_id == track_id:
                t.solo = solo
            elif solo:
                t.solo = False
        return self.store.save(project)

    def reorder_fx(
        self,
        project_id: str,
        *,
        track_id: str | None,
        from_idx: int,
        to_idx: int,
    ) -> DubProject:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        chain_slots: list[FxSlot]
        if track_id:
            track = next((t for t in project.tracks if t.track_id == track_id), None)
            if not track:
                raise KeyError(track_id)
            chain_slots = track.fx_chain
        else:
            chain_slots = project.master_fx
        specs = [FxSlotSpec(s.plugin_id, s.enabled, dict(s.params)) for s in chain_slots]
        chain = FxChain(specs)
        chain.reorder(from_idx, to_idx)
        new_slots = [
            FxSlot(s.plugin_id, s.enabled, dict(s.params)) for s in chain.slots
        ]
        if track_id:
            track.fx_chain = new_slots
        else:
            project.master_fx = new_slots
        return self.store.save(project)

    def set_segment_emotion(
        self,
        project_id: str,
        segment_id: str,
        emotion: str,
        *,
        regenerate: bool = False,
    ) -> StudioSegment:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        seg = next((s for s in project.segments if s.segment_id == segment_id), None)
        if not seg:
            raise KeyError(segment_id)
        seg.emotion = emotion.upper()
        seg.emotion_manual = True
        seg.tts_params = emotion_to_tts_params(seg.emotion)
        seg.meta["regenerate_requested"] = regenerate
        if regenerate and (seg.text or "").strip():
            try:
                out_path, tts_ms = regenerate_segment_audio(
                    self.app_dir, project_id, seg, voice=project.meta.get("voice")
                )
                labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                lbl = labels[min(len(seg.versions), 25)]
                ver = SegmentVersion(
                    version_id=str(uuid.uuid4()),
                    label=lbl,
                    audio_path=str(out_path),
                    source="tts_regen",
                    created_ms=int(time.time() * 1000),
                    meta={"emotion": seg.emotion},
                )
                seg.versions.append(ver)
                seg.active_version_id = ver.version_id
                update_segment_timing(seg, tts_ms=tts_ms, max_stretch=self._stretch_max)
                seg.meta.pop("regenerate_requested", None)
            except Exception as exc:
                seg.meta["regenerate_error"] = str(exc)
        self.store.save(project)
        return seg

    def analyze_segment_emotion(
        self,
        project_id: str,
        segment_id: str,
        *,
        audio_path: Path | None = None,
    ) -> dict[str, Any]:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        seg = next((s for s in project.segments if s.segment_id == segment_id), None)
        if not seg:
            raise KeyError(segment_id)
        emo = extract_emotion(audio_path, start_ms=seg.start_ms, end_ms=seg.end_ms)
        if not seg.emotion_manual:
            seg.emotion = str(emo.get("emotion") or "NEUTRAL")
            seg.emotion_confidence = float(emo.get("confidence") or 0)
            seg.tts_params = emotion_to_tts_params(seg.emotion)
            self.store.save(project)
        return emo

    def add_segment_version(
        self,
        project_id: str,
        segment_id: str,
        *,
        audio_path: str,
        label: str = "",
        source: str = "user",
    ) -> SegmentVersion:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        seg = next((s for s in project.segments if s.segment_id == segment_id), None)
        if not seg:
            raise KeyError(segment_id)
        labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        lbl = label or labels[min(len(seg.versions), 25)]
        ver = SegmentVersion(
            version_id=str(uuid.uuid4()),
            label=lbl,
            audio_path=audio_path,
            source=source,
            created_ms=int(time.time() * 1000),
        )
        seg.versions.append(ver)
        seg.active_version_id = ver.version_id
        try:
            from pydub import AudioSegment

            tts_ms = len(AudioSegment.from_file(audio_path))
            update_segment_timing(seg, tts_ms=tts_ms, max_stretch=self._stretch_max)
        except Exception:
            pass
        self.store.save(project)
        return ver

    def select_version(self, project_id: str, segment_id: str, version_id: str) -> StudioSegment:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        seg = next((s for s in project.segments if s.segment_id == segment_id), None)
        if not seg:
            raise KeyError(segment_id)
        if not any(v.version_id == version_id for v in seg.versions):
            raise KeyError(version_id)
        seg.active_version_id = version_id
        self.store.save(project)
        return seg

    def stretch_active_version(self, project_id: str, segment_id: str) -> dict[str, Any]:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        seg = next((s for s in project.segments if s.segment_id == segment_id), None)
        if not seg:
            raise KeyError(segment_id)
        ver = next((v for v in seg.versions if v.version_id == seg.active_version_id), None)
        if not ver or not ver.audio_path:
            return {"ok": False, "error": "no active version"}
        ratio = seg.stretch_ratio
        if ratio <= 1.001:
            return {"ok": True, "applied": False, "ratio": 1.0}
        work = self.app_dir / "output" / "dub_studio" / project_id / "stretched"
        work.mkdir(parents=True, exist_ok=True)
        out = work / f"{segment_id}_stretched.wav"
        meta = time_stretch_audio(Path(ver.audio_path), out, ratio=ratio)
        new_ver = SegmentVersion(
            version_id=str(uuid.uuid4()),
            label=ver.label + "*",
            audio_path=str(out),
            source="stretch",
            created_ms=int(time.time() * 1000),
            meta=meta,
        )
        seg.versions.append(new_ver)
        seg.active_version_id = new_ver.version_id
        update_segment_timing(seg, tts_ms=int(meta.get("output_ms") or seg.tts_ms))
        self.store.save(project)
        return {"ok": True, **meta}

    def preview_fx(
        self,
        project_id: str,
        *,
        input_path: Path,
        fx_slots: list[dict[str, Any]],
    ) -> str:
        """In-memory preview job id — non-blocking FX render to RAM buffer path."""
        work = self.app_dir / "output" / "dub_studio" / project_id / "preview"
        work.mkdir(parents=True, exist_ok=True)
        specs = [
            FxSlotSpec(s.get("plugin_id", ""), bool(s.get("enabled", True)), dict(s.get("params") or {}))
            for s in fx_slots
            if s.get("plugin_id")
        ]
        chain = FxChain(specs)
        job_id = str(uuid.uuid4())
        out = work / f"preview_{job_id}.wav"

        def _done(result):
            try:
                _PREVIEW_BUFFERS[job_id] = Path(result.output_path).read_bytes()
            except Exception:
                pass

        self.fx_pipeline.submit(chain, input_path, work, on_done=_done)
        return job_id

    def get_preview_buffer(self, job_id: str) -> bytes | None:
        return _PREVIEW_BUFFERS.get(job_id)

    def export_project(self, project_id: str, *, fmt: str = "wav") -> Path:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        out = export_timeline_wav(project, self.app_dir, format=fmt)
        project.meta["last_export"] = str(out)
        self.store.save(project)
        return out

    def analyze_all_emotions(self, project_id: str, *, original_audio: Path | None = None) -> int:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        count = 0
        for seg in project.segments:
            if seg.emotion_manual:
                continue
            emo = extract_emotion(original_audio, start_ms=seg.start_ms, end_ms=seg.end_ms)
            seg.emotion = str(emo.get("emotion") or "NEUTRAL")
            seg.emotion_confidence = float(emo.get("confidence") or 0)
            seg.tts_params = emotion_to_tts_params(seg.emotion)
            count += 1
        self.store.save(project)
        return count

    def update_track(
        self,
        project_id: str,
        track_id: str,
        *,
        muted: bool | None = None,
        solo: bool | None = None,
        volume: float | None = None,
        pan: float | None = None,
        monitor: bool | None = None,
        record_enabled: bool | None = None,
    ) -> StudioTrack:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        track = next((t for t in project.tracks if t.track_id == track_id), None)
        if not track:
            raise KeyError(track_id)
        if muted is not None:
            track.muted = muted
        if solo is not None:
            if solo:
                for t in project.tracks:
                    t.solo = t.track_id == track_id
            else:
                track.solo = False
        if volume is not None:
            track.volume = max(0.0, min(2.0, float(volume)))
        if pan is not None:
            track.pan = max(-1.0, min(1.0, float(pan)))
        if monitor is not None:
            track.monitor = monitor
        if record_enabled is not None:
            track.record_enabled = record_enabled
        self.store.save(project)
        return track

    def add_track_plugin(self, project_id: str, track_id: str, plugin_id: str) -> StudioTrack:
        project = self.store.load(project_id)
        if not project:
            raise KeyError(project_id)
        track = next((t for t in project.tracks if t.track_id == track_id), None)
        if not track:
            raise KeyError(track_id)
        slot = FxSlot(plugin_id=plugin_id, enabled=True, params={})
        track.plugin_slots.append(slot)
        track.fx_chain.append(slot)
        self.store.save(project)
        return track

    def list_track_plugins(self) -> list[dict[str, Any]]:
        from engines.dub_studio.plugin_host import list_all_plugins

        return list_all_plugins()


def get_dub_studio_service(app_dir: Path | None = None) -> DubStudioService:
    base = Path(app_dir or Path(__file__).resolve().parents[2])
    key = str(base.resolve())
    with _LOCK:
        if key not in _SERVICES:
            _SERVICES[key] = DubStudioService(base)
        return _SERVICES[key]
