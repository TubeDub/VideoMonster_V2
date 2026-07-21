"""Pipeline Watchdog — detect stalls, reconcile state, terminate hung stages (TZ).

Monitors Translation, TTS, Timing/Sync, Mix, MP4 stages.
Never allows infinite 'running' without terminal state.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.pipeline_watchdog")

# Seconds without progress before stall detection (per stage family).
STALL_IDLE_SEC: dict[str, float] = {
    "translate": 300.0,
    "translation": 300.0,
    "ai_core": 300.0,
    # Long Edge/offline group can exceed 90s with no on_group_done (e0e03d).
    "tts": 300.0,
    "slot_fit": 180.0,
    "voice_verification": 300.0,
    "adaptation": 300.0,
    "post_tts_qa": 240.0,
    "timing": 90.0,
    "synchronization": 90.0,
    "studio": 90.0,
    "mix": 90.0,
    "dub": 120.0,
    "mp4": 120.0,
    "transcribe": 600.0,
    "segment_prep": 180.0,
    "extract_audio": 180.0,
    "preparing": 300.0,
}
DEFAULT_STALL_IDLE_SEC = 120.0
# Stages that may block the worker thread for minutes on CPU (Whisper load, init).
_CPU_HEAVY_STAGES = frozenset(
    {
        "preparing",
        "extract_audio",
        "transcribe",
        "segment_prep",
        "translate",
        "translation",
        "ai_core",
    }
)
_CPU_HEAVY_THREAD_MULTIPLIER = 1.5
WATCHDOG_POLL_SEC = 5.0

_TERMINAL = frozenset({"done", "error", "cancelled", "stalled", "studio_ready"})

# Intentional human pauses — never PIPELINE_STALLED while waiting for user.
_USER_WAIT_STATUSES = frozenset({"translation_review", "paused", "editing"})

_STAGE_LABELS = {
    "ru": {
        "translate": "Перевод",
        "tts": "Озвучка (TTS)",
        "slot_fit": "Подгонка под тайминг",
        "voice_verification": "Проверка озвучки",
        "timing": "Синхронизация",
        "studio": "Сведение",
        "dub": "Сборка MP4",
    },
    "en": {
        "translate": "Translation",
        "tts": "TTS",
        "slot_fit": "Slot fit",
        "voice_verification": "Voice verification",
        "timing": "Synchronization",
        "studio": "Mix",
        "dub": "MP4 export",
    },
    "uk": {
        "translate": "Переклад",
        "tts": "Озвучка (TTS)",
        "slot_fit": "Підгонка під таймінг",
        "voice_verification": "Перевірка озвучки",
        "timing": "Синхронізація",
        "studio": "Зведення",
        "dub": "Збірка MP4",
    },
}


@dataclass
class StageSnapshot:
    stage: str = ""
    status: str = "running"
    started_at: float = 0.0
    last_progress_at: float = 0.0
    segments_done: int = 0
    total_segments: int = 0
    current_segment: int = 0
    progress_pct: float = 0.0
    thread_id: int = 0
    thread_alive: bool = False
    substep: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def idle_sec(self) -> float:
        ref = self.last_progress_at or self.started_at
        if ref <= 0:
            return 0.0
        return max(0.0, time.time() - ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "started_at": self.started_at,
            "last_progress_at": self.last_progress_at,
            "segments_done": self.segments_done,
            "total_segments": self.total_segments,
            "current_segment": self.current_segment,
            "progress_pct": self.progress_pct,
            "idle_sec": round(self.idle_sec(), 1),
            "thread_id": self.thread_id,
            "thread_alive": self.thread_alive,
            "substep": self.substep,
            **self.extra,
        }


class PipelineWatchdog:
    """Background checker for one dub task."""

    def __init__(
        self,
        task_id: str,
        *,
        app_dir: Path | None = None,
        on_stall: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.task_id = str(task_id)
        self.app_dir = app_dir or Path(__file__).resolve().parents[1]
        self._on_stall = on_stall
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._stage = StageSnapshot()
        self._last_segments_done = -1
        self._last_progress_pct = -1.0
        self._ticks = 0
        self._stall_reported = False
        self._llm_recovery_attempts = 0
        self._LLM_RECOVERY_MAX = 3

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"pipeline-watchdog-{self.task_id[:8]}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def stage_start(self, stage: str, *, progress_pct: float = 0.0) -> None:
        now = time.time()
        with self._lock:
            self._stage = StageSnapshot(
                stage=str(stage or ""),
                status="running",
                started_at=now,
                last_progress_at=now,
                progress_pct=float(progress_pct),
                thread_id=self._pipeline_thread_id(),
            )
            self._last_segments_done = -1
            self._last_progress_pct = float(progress_pct)

    def heartbeat(self, **fields: Any) -> None:
        """Record progress — called from _update_progress_detail."""
        now = time.time()
        with self._lock:
            if fields.get("phase"):
                self._stage.stage = str(fields["phase"])
            if self._stage.started_at <= 0:
                self._stage.started_at = now
            seg_done = fields.get("segments_done")
            cur_seg = fields.get("current_segment")
            total = fields.get("total_segments")
            progressed = False
            if seg_done is not None:
                seg_i = int(seg_done)
                if seg_i != self._last_segments_done:
                    self._stage.segments_done = seg_i
                    self._last_segments_done = seg_i
                    progressed = True
            if cur_seg is not None:
                self._stage.current_segment = int(cur_seg)
                progressed = True
            if total is not None:
                self._stage.total_segments = int(total)
            if fields.get("timing_substep"):
                self._stage.substep = str(fields["timing_substep"])
                progressed = True
            if fields.get("tts_substep"):
                self._stage.substep = str(fields["tts_substep"])
                progressed = True
            if fields.get("verification_attempt"):
                self._stage.substep = "voice_verify"
                progressed = True
            if fields.get("verification_route"):
                progressed = True
            if progressed or fields:
                self._stage.last_progress_at = now
            self._stage.thread_id = self._pipeline_thread_id()
            self._stage.extra.update(
                {k: v for k, v in fields.items() if k not in ("phase",)}
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = self._stage.to_dict()
            snap["task_id"] = self.task_id
            snap["ticks"] = self._ticks
            return snap

    def _pipeline_thread_id(self) -> int:
        try:
            from engines.dub_task_state import get_pipeline_thread

            t = get_pipeline_thread(self.task_id)
            return t.ident or 0 if t else 0
        except Exception:
            return 0

    def _pipeline_thread_alive(self) -> bool:
        try:
            from engines.dub_task_state import get_pipeline_thread

            t = get_pipeline_thread(self.task_id)
            return bool(t and t.is_alive())
        except Exception:
            return False

    def _run(self) -> None:
        while not self._stop.wait(WATCHDOG_POLL_SEC):
            try:
                self._tick()
            except Exception as exc:
                logger.debug("watchdog tick error task=%s: %s", self.task_id, exc)

    def _tick(self) -> None:
        from engines.dub_task_state import (
            AUTO_TASKS,
            STATE_LOCK,
            is_cancel_requested,
        )

        self._ticks += 1
        with STATE_LOCK:
            task = AUTO_TASKS.get(self.task_id)
            if not task:
                self.stop()
                return
            status = str(task.get("status") or "")
            if status in _TERMINAL:
                self.stop()
                return
            if is_cancel_requested(self.task_id):
                return
            from engines.dub_task_state import AUTO_TASK_CONTROLS

            info = task.get("info") or {}
            control = AUTO_TASK_CONTROLS.get(self.task_id) or {}
            ui_lang = str(task.get("ui_lang") or info.get("ui_lang") or "ru")
            progress = float(task.get("progress") or 0)
            step = str(task.get("step") or self._stage.stage or "")
            detail = dict(info.get("progress_detail") or {})
            awaiting_review = bool(control.get("awaiting_translation_review"))
            user_wait = (
                status in _USER_WAIT_STATUSES
                or awaiting_review
                or step == "translation_review"
            )

        # Translation Review / Manual Review: worker blocked on purpose (TPS).
        # Never PIPELINE_STALLED while waiting for the user.
        if user_wait:
            with self._lock:
                self._stage.last_progress_at = time.time()
                self._stage.stage = "translation_review"
            return

        with self._lock:
            if progress != self._last_progress_pct:
                self._stage.progress_pct = progress
                self._last_progress_pct = progress
                self._stage.last_progress_at = time.time()
            detail_phase_tick = str(detail.get("phase") or "")
            if step and step != self._stage.stage:
                # task.step can lag behind progress_detail.phase (e.g. step=tts during slot_fit).
                if not (detail_phase_tick and detail_phase_tick != step):
                    self.stage_start(step, progress_pct=progress)
            self._stage.thread_alive = self._pipeline_thread_alive()

        idle = self._stage.idle_sec()
        effective_step = step or self._stage.stage
        detail_phase = str(detail.get("phase") or "")
        tts_sub = str(detail.get("tts_substep") or "")
        if tts_sub == "voice_verify" or detail_phase == "voice_verification":
            effective_step = "voice_verification"
        elif detail_phase in STALL_IDLE_SEC:
            effective_step = detail_phase
        elif detail_phase and detail_phase != step:
            # task.step may stay on "tts" while slot_fit / QA / verification run.
            effective_step = detail_phase
        threshold = STALL_IDLE_SEC.get(
            effective_step,
            STALL_IDLE_SEC.get(self._stage.stage, DEFAULT_STALL_IDLE_SEC),
        )
        if self._stage.thread_alive and effective_step in _CPU_HEAVY_STAGES:
            threshold *= _CPU_HEAVY_THREAD_MULTIPLIER

        # Pipeline thread died but task still "running" — reconcile immediately.
        if status == "running" and not self._stage.thread_alive and self._ticks > 2:
            self._handle_stall(
                ui_lang,
                reason_code="PIPELINE_THREAD_EXITED",
                probable_cause="worker_thread_finished_without_status_update",
                idle_sec=idle,
                step=step,
            )
            return

        if idle < threshold:
            return

        if self._stall_reported:
            return

        # Still working? extend if thread alive and recent detail heartbeat.
        detail_hb = float(detail.get("last_heartbeat_at") or 0)
        hb_age = time.time() - detail_hb if detail_hb else float("inf")
        # TTS heartbeats may be sparse (one per group); use full threshold.
        hb_window = (
            threshold if effective_step == "tts" else min(threshold, 120.0)
        )
        if self._stage.thread_alive and detail_hb and hb_age < hb_window:
            with self._lock:
                self._stage.last_progress_at = time.time()
            return

        if effective_step in ("translate", "translation") and self._llm_recovery_attempts < self._LLM_RECOVERY_MAX:
            if self._try_llm_recovery(ui_lang, detail):
                return

        probable = self._guess_cause(effective_step, detail)
        self._handle_stall(
            ui_lang,
            reason_code="PIPELINE_STALLED",
            probable_cause=probable,
            idle_sec=idle,
            step=effective_step,
            detail=detail,
        )

    def _try_llm_recovery(self, ui_lang: str, detail: dict[str, Any]) -> bool:
        """Attempt LLM recovery before declaring translate stall."""
        from engines.llm_diagnostics import attempt_llm_recovery, recovery_live_message

        idx = self._llm_recovery_attempts
        result = attempt_llm_recovery(self.task_id, attempt_index=idx, app_dir=self.app_dir)
        self._llm_recovery_attempts += 1
        step = str(result.get("step") or "")
        msg = recovery_live_message(step, ui_lang)
        logger.warning(
            "Task %s LLM recovery #%d step=%s ok=%s detail=%s",
            self.task_id,
            idx + 1,
            step,
            result.get("ok"),
            result.get("detail"),
        )
        try:
            from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
            from engines.pipeline_progress_tracker import enrich_progress_fields

            fields = enrich_progress_fields(
                self.task_id,
                phase="translate",
                live_message=msg,
                llm_recovery_step=step,
                llm_recovery_ok=bool(result.get("ok")),
            )
            with STATE_LOCK:
                task = AUTO_TASKS.get(self.task_id)
                if task:
                    task.setdefault("info", {}).setdefault("progress_detail", {}).update(fields)
            self.heartbeat(**fields)
        except Exception:
            pass
        with self._lock:
            self._stage.last_progress_at = time.time()
        return True

    def _guess_cause(self, step: str, detail: dict[str, Any]) -> str:
        if step in ("translate", "translation"):
            return "llm_slow_or_unresponsive"
        if step == "voice_verification":
            return "voice_verification_slow"
        if step == "slot_fit":
            return "slot_fit_slow"
        if step == "tts":
            sub = str(detail.get("tts_substep") or "")
            if sub == "voice_verify":
                return "voice_verification_slow"
            return "tts_engine_slow_or_blocked"
        if step in ("timing", "dub"):
            return "ffmpeg_or_audio_processing_blocked"
        if step == "transcribe":
            return "whisper_model_slow_or_gpu_busy"
        sub = str(detail.get("tts_substep") or detail.get("timing_substep") or "")
        if sub:
            return f"substep_{sub}_blocked"
        return "no_progress_timeout"

    def _handle_stall(
        self,
        ui_lang: str,
        *,
        reason_code: str,
        probable_cause: str,
        idle_sec: float,
        step: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._stall_reported:
            return
        self._stall_reported = True

        labels = _STAGE_LABELS.get(ui_lang, _STAGE_LABELS["ru"])
        stage_label = labels.get(step, step or "pipeline")
        seg = self._stage.current_segment or self._stage.segments_done
        llm_ctx: dict[str, Any] = {}
        if step in ("translate", "translation"):
            try:
                from engines.llm_diagnostics import build_llm_stall_message, collect_llm_stall_context

                llm_ctx = collect_llm_stall_context(self.task_id, progress_detail=detail or {})
                msg = build_llm_stall_message(llm_ctx, idle_sec=idle_sec, lang=ui_lang)
            except Exception:
                msg = self._stall_message(ui_lang, stage_label, seg, idle_sec, probable_cause)
        else:
            msg = self._stall_message(ui_lang, stage_label, seg, idle_sec, probable_cause)

        report = self._build_stall_report(
            reason_code=reason_code,
            message=msg,
            probable_cause=probable_cause,
            idle_sec=idle_sec,
            step=step,
            stage_label=stage_label,
            llm_diagnostics=llm_ctx,
            detail=detail or {},
        )
        self._save_report(report)

        if self._on_stall:
            try:
                self._on_stall(self.task_id, report)
            except Exception as exc:
                logger.error("on_stall callback failed: %s", exc)
        self.stop()

    def _stall_message(
        self,
        ui_lang: str,
        stage_label: str,
        segment: int,
        idle_sec: float,
        cause: str,
    ) -> str:
        mins = int(idle_sec // 60)
        sec = int(idle_sec % 60)
        time_str = f"{mins} мин {sec} с" if ui_lang != "en" else f"{mins}m {sec}s"
        seg_part = ""
        if segment:
            if ui_lang == "en":
                seg_part = f" Segment {segment}."
            elif ui_lang == "uk":
                seg_part = f" Сегмент {segment}."
            else:
                seg_part = f" Сегмент {segment}."
        causes = {
            "llm_slow_or_unresponsive": {
                "ru": "Возможная причина: модель перевода не отвечает (LLM).",
                "uk": "Можлива причина: модель перекладу не відповідає (LLM).",
                "en": "Likely cause: translation model (LLM) not responding.",
            },
            "tts_engine_slow_or_blocked": {
                "ru": "Возможная причина: движок TTS завис или сеть недоступна.",
                "uk": "Можлива причина: рушій TTS завис або мережа недоступна.",
                "en": "Likely cause: TTS engine hung or network unavailable.",
            },
            "voice_verification_slow": {
                "ru": "Возможная причина: длительная перепроверка озвучки (ASR + повторный синтез).",
                "uk": "Можлива причина: тривала переперевірка озвучки (ASR + повторний синтез).",
                "en": "Likely cause: long voice verification (ASR + re-synthesis retries).",
            },
            "slot_fit_slow": {
                "ru": "Возможная причина: подгонка аудио под тайминг занимает больше обычного.",
                "uk": "Можлива причина: підгонка аудіо під таймінг триває довше за звичай.",
                "en": "Likely cause: slot-fit audio processing slower than usual.",
            },
            "ffmpeg_or_audio_processing_blocked": {
                "ru": "Возможная причина: FFmpeg или обработка аудио заблокированы.",
                "uk": "Можлива причина: FFmpeg або обробка аудіо заблоковані.",
                "en": "Likely cause: FFmpeg or audio processing blocked.",
            },
            "whisper_model_slow_or_gpu_busy": {
                "ru": "Возможная причина: Whisper долго обрабатывает или не хватает ресурсов.",
                "uk": "Можлива причина: Whisper довго обробляє або бракує ресурсів.",
                "en": "Likely cause: Whisper slow or insufficient resources.",
            },
            "worker_thread_finished_without_status_update": {
                "ru": "Возможная причина: фоновый поток завершился без обновления статуса.",
                "uk": "Можлива причина: фоновий потік завершився без оновлення статусу.",
                "en": "Likely cause: worker thread exited without updating status.",
            },
        }
        cause_msg = causes.get(cause, {}).get(ui_lang, causes.get(cause, {}).get("ru", ""))

        if ui_lang == "en":
            return (
                f"Stage stalled: {stage_label}.{seg_part} "
                f"No activity for {time_str}. {cause_msg}"
            )
        if ui_lang == "uk":
            return (
                f"Етап завис: {stage_label}.{seg_part} "
                f"Немає активності {time_str}. {cause_msg}"
            )
        return (
            f"Этап завис: {stage_label}.{seg_part} "
            f"Нет активности {time_str}. {cause_msg}"
        )

    def _build_stall_report(
        self,
        *,
        reason_code: str,
        message: str,
        probable_cause: str,
        idle_sec: float,
        step: str,
        stage_label: str,
        llm_diagnostics: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active_model: dict[str, Any] = {}
        try:
            from engines.llm_adaptation_mode import detect_capabilities

            caps = detect_capabilities()
            active_model = {
                "model": caps.get("model"),
                "provider": caps.get("provider"),
                "model_display": caps.get("model"),
            }
            if llm_diagnostics:
                active_model["model_display"] = llm_diagnostics.get("model_display") or active_model.get("model")
        except Exception:
            pass

        if llm_diagnostics:
            active_model.update(
                {
                    k: llm_diagnostics.get(k)
                    for k in (
                        "provider_label",
                        "segment",
                        "total_segments",
                        "chars_sent",
                        "wait_sec",
                        "attempts",
                        "timeout",
                        "ollama",
                    )
                    if llm_diagnostics.get(k) is not None
                }
            )

        threads: list[dict[str, Any]] = []
        try:
            for t in threading.enumerate():
                if self.task_id[:8] in (t.name or "") or "pipeline" in (t.name or "").lower():
                    threads.append({"name": t.name, "alive": t.is_alive(), "ident": t.ident})
        except Exception:
            pass

        with self._lock:
            stage_snap = self._stage.to_dict()

        return {
            "schema": "tubedub.pipeline_stall.v2",
            "task_id": self.task_id,
            "reason_code": reason_code,
            "message": message,
            "probable_cause": probable_cause,
            "idle_sec": round(idle_sec, 1),
            "step": step,
            "stage_label": stage_label,
            "stage_snapshot": stage_snap,
            "active_model": active_model,
            "llm_diagnostics": llm_diagnostics or {},
            "progress_detail": detail or {},
            "recovery_attempts": self._llm_recovery_attempts,
            "threads": threads,
            "stack_summary": traceback.format_stack()[-8:],
            "recorded_at": time.time(),
        }

    def _save_report(self, report: dict[str, Any]) -> None:
        out_dir = self.app_dir / "output" / "diagnostics" / self.task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "pipeline_stall.json"
        try:
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("stall report save failed: %s", exc)


_WATCHDOGS: dict[str, PipelineWatchdog] = {}
_WD_LOCK = threading.RLock()


def start_pipeline_watchdog(task_id: str, *, app_dir: Path | None = None) -> PipelineWatchdog:
    from engines.pipeline_watchdog import stall_pipeline_task

    rid = str(task_id or "")
    with _WD_LOCK:
        wd = _WATCHDOGS.get(rid)
        if wd:
            wd.stop()
        wd = PipelineWatchdog(rid, app_dir=app_dir, on_stall=stall_pipeline_task)
        _WATCHDOGS[rid] = wd
        wd.start()
        return wd


def stop_pipeline_watchdog(task_id: str) -> None:
    rid = str(task_id or "")
    with _WD_LOCK:
        wd = _WATCHDOGS.pop(rid, None)
    if wd:
        wd.stop()


def get_pipeline_watchdog(task_id: str) -> PipelineWatchdog | None:
    return _WATCHDOGS.get(str(task_id or ""))


def watchdog_stage_start(task_id: str, stage: str, *, progress_pct: float = 0.0) -> None:
    wd = get_pipeline_watchdog(task_id)
    if wd:
        wd.stage_start(stage, progress_pct=progress_pct)


def watchdog_heartbeat(task_id: str, **fields: Any) -> None:
    fields = dict(fields)
    fields["last_heartbeat_at"] = time.time()
    wd = get_pipeline_watchdog(task_id)
    if wd:
        wd.heartbeat(**fields)


def stall_pipeline_task(task_id: str, report: dict[str, Any]) -> None:
    """Apply stalled terminal state — called by watchdog."""
    from engines.dub_task_state import (
        AUTO_TASK_CONTROLS,
        AUTO_TASKS,
        STATE_LOCK,
        cancel_pipeline_runtime,
        request_cancel,
    )

    request_cancel(task_id, reason="stalled")
    cancel_pipeline_runtime(task_id, join_timeout=3.0)

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        if str(task.get("status")) in _TERMINAL:
            return
        task["status"] = "stalled"
        info = task.setdefault("info", {})
        try:
            from engines.ai_core.architecture_validation import pipeline_checkpoint

            info["pipeline_checkpoint"] = pipeline_checkpoint(info)
        except Exception:
            pass
        info["pipeline_stall"] = report
        llm_diag = report.get("llm_diagnostics") or {}
        info["pipeline_error"] = {
            "title": "Обработка остановлена",
            "reason": report.get("message"),
            "reason_short": report.get("message"),
            "error_code": report.get("reason_code"),
            "stage": report.get("stage_label"),
            "segment": llm_diag.get("segment_label") or llm_diag.get("segment"),
            "llm_diagnostics": llm_diag,
        }
        info.setdefault("errors", []).append(str(report.get("message") or "stalled"))
        control = AUTO_TASK_CONTROLS.get(task_id)
        if control:
            control["state"] = "stalled"

    logger.warning(
        "Task %s STALLED step=%s idle=%.1fs cause=%s",
        task_id,
        report.get("step"),
        report.get("idle_sec"),
        report.get("probable_cause"),
    )

    try:
        from engines.ai_core.architecture_validation import merge_ux_event

        merge_ux_event(task_id, event="stalled", app_dir=Path(__file__).resolve().parents[1])
    except Exception:
        pass
