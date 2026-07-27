"""Live Translation pipeline — chunk-based processing with SSE events."""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from engines.live.audio import ensure_pcm_wav, extract_chunk_wav, wav_duration_sec
from engines.live.config import live_config
from engines.live.ingest import resolve_ingest
from engines.live.subtitles import SubtitleCue
from engines.live.translate import translate_phrase_live
from engines.platform_diagnostics.sink import PlatformTraceSink

logger = logging.getLogger("tubedub.engines.live")

_SESSIONS: dict[str, "LiveSession"] = {}
_LOCK = threading.Lock()


@dataclass
class LiveEvent:
    type: str
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "ts_ms": self.ts_ms or int(time.time() * 1000),
            "payload": self.payload,
        }


@dataclass
class LiveSession:
    session_id: str
    app_dir: Path
    source_uri: str
    src_lang: str
    tgt_lang: str
    voice: str
    status: str = "starting"
    error: str = ""
    events: list[LiveEvent] = field(default_factory=list)
    subtitles: list[SubtitleCue] = field(default_factory=list)
    sink: PlatformTraceSink | None = None
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def push(self, ev: LiveEvent) -> None:
        with self._lock:
            self.events.append(ev)

    def iter_events(self, after: int = 0) -> Iterator[LiveEvent]:
        with self._lock:
            for ev in self.events[after:]:
                yield ev


class LiveTranslationPipeline:
    """Independent live translation orchestrator (does not touch batch dub)."""

    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)

    def start(
        self,
        source_uri: str,
        *,
        tgt_lang: str = "ru",
        src_lang: str | None = None,
        voice: str = "",
    ) -> str:
        from engines.live.preflight import preflight_live

        cfg = live_config()
        pf = preflight_live(require_stt=True, require_tts=not cfg.simulate_only)
        session_id = uuid.uuid4().hex[:12]
        sink = PlatformTraceSink(self.app_dir, module="live", session_id=session_id)
        session = LiveSession(
            session_id=session_id,
            app_dir=self.app_dir,
            source_uri=source_uri.strip(),
            src_lang=(src_lang or "auto").split("-")[0].lower(),
            tgt_lang=(tgt_lang or "ru").split("-")[0].lower(),
            voice=voice or "ru-RU-DmitryNeural",
            sink=sink,
        )
        with _LOCK:
            _SESSIONS[session_id] = session

        session.push(
            LiveEvent(
                type="session_started",
                session_id=session_id,
                payload={"source": source_uri, "tgt_lang": session.tgt_lang, "preflight": pf},
            )
        )
        if not pf.get("ok"):
            session.status = "error"
            session.error = "; ".join(pf.get("issues") or ["preflight failed"])
            session.push(
                LiveEvent(
                    type="error",
                    session_id=session_id,
                    payload={
                        "stage": "preflight",
                        "message": session.error,
                        "issues": pf.get("issues"),
                        "engines": pf.get("engines"),
                    },
                )
            )
            return session_id

        t = threading.Thread(
            target=self._run_session,
            args=(session, cfg),
            daemon=True,
            name=f"live-{session_id}",
        )
        session._thread = t
        t.start()
        return session_id

    def stop(self, session_id: str) -> None:
        with _LOCK:
            session = _SESSIONS.get(session_id)
        if session:
            session._stop.set()
            session.status = "stopping"

    def get_session(self, session_id: str) -> LiveSession | None:
        with _LOCK:
            return _SESSIONS.get(session_id)

    def subscribe_events(
        self, session_id: str, *, after: int = 0, timeout_sec: float = 120.0
    ) -> Iterator[dict[str, Any]]:
        deadline = time.time() + timeout_sec
        cursor = after
        while time.time() < deadline:
            session = self.get_session(session_id)
            if not session:
                yield LiveEvent(
                    type="error",
                    session_id=session_id,
                    payload={"message": "Session not found"},
                ).to_dict()
                return
            batch = list(session.iter_events(cursor))
            for ev in batch:
                cursor += 1
                yield ev.to_dict()
            if session.status in ("completed", "error", "stopped") and cursor >= len(
                session.events
            ):
                yield LiveEvent(
                    type="session_end",
                    session_id=session_id,
                    payload={"status": session.status, "error": session.error},
                ).to_dict()
                return
            if batch:
                continue
            time.sleep(0.25)

    def diagnostics(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        if not session or not session.sink:
            return {"ok": False, "error": "not_found"}
        snap = session.sink.snapshot()
        snap["status"] = session.status
        snap["subtitle_count"] = len(session.subtitles)
        return snap

    def _run_session(self, session: LiveSession, cfg: Any) -> None:
        work_dir = Path(tempfile.mkdtemp(prefix=f"live_{session.session_id}_"))
        try:
            from engines.platform_diagnostics.trace import trace_stage

            with trace_stage(
                session.sink,
                stage="live.ingest",
                module="live",
                session_id=session.session_id,
                input_preview=session.source_uri,
            ) as tr:
                ing = resolve_ingest(session.source_uri, work_dir=work_dir)
                tr.output_preview = str(ing)
                tr.engine = ing.engine
                if not ing.ok:
                    session.status = "error"
                    session.error = ing.error
                    session.push(
                        LiveEvent(
                            type="error",
                            session_id=session.session_id,
                            payload={"stage": "ingest", "message": ing.error},
                        )
                    )
                    return

            media_source = ing.local_path or ing.stream_url
            if ing.local_path:
                pcm_ok, pcm_path, pcm_err = ensure_pcm_wav(
                    ing.local_path, work_dir=work_dir
                )
            else:
                pcm_ok, pcm_path, pcm_err = ensure_pcm_wav(
                    media_source, work_dir=work_dir
                )

            if not pcm_ok:
                session.status = "error"
                session.error = pcm_err
                session.push(
                    LiveEvent(
                        type="error",
                        session_id=session.session_id,
                        payload={"stage": "demux", "message": pcm_err},
                    )
                )
                return

            duration = wav_duration_sec(pcm_path)
            session.status = "running"
            session.push(
                LiveEvent(
                    type="ready",
                    session_id=session.session_id,
                    payload={
                        "duration_sec": duration,
                        "chunk_sec": cfg.chunk_seconds,
                        "source_type": ing.source_type,
                    },
                )
            )

            context: list[str] = []
            start = 0.0
            chunk_i = 0
            while start < duration and not session._stop.is_set():
                chunk_i += 1
                ok, chunk_wav, cerr = extract_chunk_wav(
                    pcm_path,
                    start_sec=start,
                    duration_sec=cfg.chunk_seconds,
                    work_dir=work_dir,
                    chunk_index=chunk_i,
                )
                if not ok:
                    session.push(
                        LiveEvent(
                            type="warn",
                            session_id=session.session_id,
                            payload={"stage": "chunk", "message": cerr},
                        )
                    )
                    break

                stt_text = ""
                detected = session.src_lang
                stt_ms = 0.0
                t_stt = time.perf_counter()
                try:
                    from engines.stt_engine import transcribe

                    stt_text, _, _, detected = transcribe(
                        chunk_wav,
                        language=None if session.src_lang == "auto" else session.src_lang,
                        model_size=cfg.stt_model,
                        word_timestamps=False,
                    )
                    stt_text = " ".join(str(stt_text or "").split()).strip()
                    if session.src_lang == "auto" and detected:
                        session.src_lang = detected.split("-")[0].lower()
                except Exception as e:
                    logger.warning("Live STT chunk failed: %s", e)
                    stt_text = ""
                stt_ms = (time.perf_counter() - t_stt) * 1000.0

                session.sink.log(
                    stage="live.stt.final",
                    input_preview=f"chunk@{start:.1f}s",
                    output_preview=stt_text,
                    duration_ms=stt_ms,
                    engine=f"faster-whisper-{cfg.stt_model}",
                )

                if stt_text:
                    session.push(
                        LiveEvent(
                            type="stt",
                            session_id=session.session_id,
                            payload={
                                "chunk": chunk_i,
                                "start_sec": start,
                                "text": stt_text,
                                "lang": session.src_lang,
                            },
                        )
                    )

                    mt = translate_phrase_live(
                        stt_text,
                        src_lang=session.src_lang,
                        tgt_lang=session.tgt_lang,
                        app_dir=session.app_dir,
                        context=context,
                        use_naturalizer=cfg.use_naturalizer,
                        use_enterprise=cfg.use_enterprise,
                    )
                    translated = str(mt.get("text") or "").strip()
                    context.append(translated)

                    session.sink.log(
                        stage="live.translate",
                        input_preview=stt_text,
                        output_preview=translated,
                        duration_ms=float(mt.get("elapsed_ms") or 0),
                        engine=str(mt.get("engine") or ""),
                        router_reason=str(mt.get("router_reason") or ""),
                    )

                    session.push(
                        LiveEvent(
                            type="translated",
                            session_id=session.session_id,
                            payload={
                                "chunk": chunk_i,
                                "source": stt_text,
                                "text": translated,
                                "engine": mt.get("engine"),
                                "router_reason": mt.get("router_reason"),
                            },
                        )
                    )

                    if cfg.subtitles and translated:
                        cue = SubtitleCue(
                            start_ms=int(start * 1000),
                            end_ms=int((start + cfg.chunk_seconds) * 1000),
                            text=translated,
                        )
                        session.subtitles.append(cue)
                        session.push(
                            LiveEvent(
                                type="subtitle",
                                session_id=session.session_id,
                                payload={
                                    "start_ms": cue.start_ms,
                                    "end_ms": cue.end_ms,
                                    "text": cue.text,
                                },
                            )
                        )

                    tts_path = ""
                    if translated and not cfg.simulate_only:
                        tts_ms = 0.0
                        t0 = time.perf_counter()
                        try:
                            from engines.tts import generate_audio, get_output_path

                            files = generate_audio(
                                translated,
                                voice=session.voice,
                            )
                            if files:
                                tts_path = str(get_output_path(files[0]))
                        except Exception as e:
                            logger.warning("Live TTS failed: %s", e)
                        tts_ms = (time.perf_counter() - t0) * 1000.0
                        session.sink.log(
                            stage="live.tts",
                            input_preview=translated[:120],
                            output_preview=tts_path or "",
                            duration_ms=tts_ms,
                            engine="edge-tts",
                        )
                        session.push(
                            LiveEvent(
                                type="tts",
                                session_id=session.session_id,
                                payload={
                                    "chunk": chunk_i,
                                    "text": translated,
                                    "audio_path": tts_path,
                                },
                            )
                        )

                start += cfg.chunk_seconds

            session.status = "stopped" if session._stop.is_set() else "completed"
            session.push(
                LiveEvent(
                    type="completed",
                    session_id=session.session_id,
                    payload={"chunks": chunk_i, "status": session.status},
                )
            )
        except Exception as e:
            logger.exception("Live session failed")
            session.status = "error"
            session.error = str(e)
            session.sink.log(stage="live.error", error=str(e))
            session.push(
                LiveEvent(
                    type="error",
                    session_id=session.session_id,
                    payload={"message": str(e)},
                )
            )
        finally:
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass
