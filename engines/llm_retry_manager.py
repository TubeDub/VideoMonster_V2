"""LLM Retry Manager — health checks, retries, fallback models, rich diagnostics."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("tubedub.llm_retry")

# ── Config (env-overridable) ────────────────────────────────────────────────


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = True) -> bool:
    raw = str(os.getenv(key, "1" if default else "0")).strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 3
    retry_delay_sec: float = 5.0
    call_timeout_sec: float = 45.0
    slow_warn_sec: float = 25.0
    health_check: bool = True
    restart_ollama_on_unreachable: bool = True
    fallback_enabled: bool = True
    progress_interval_sec: float = 10.0

    @classmethod
    def from_env(cls, *, cpu_only: bool = False, model: str = "") -> RetryConfig:
        default_timeout = 45.0 if cpu_only else 15.0
        if cpu_only and model:
            try:
                from engines.llm_adaptation_mode import _model_param_billions

                param_b = _model_param_billions(model)
                if param_b >= 13:
                    default_timeout = 180.0
                elif param_b >= 7:
                    default_timeout = 120.0
            except Exception:
                pass
        return cls(
            max_retries=max(1, _env_int("VM_LLM_MAX_RETRIES", 3)),
            retry_delay_sec=max(0.0, _env_float("VM_LLM_RETRY_DELAY_SEC", 5.0)),
            call_timeout_sec=max(5.0, _env_float("VM_LLM_CALL_TIMEOUT", default_timeout)),
            slow_warn_sec=max(5.0, _env_float("VM_LLM_SLOW_WARN_SEC", 25.0)),
            health_check=_env_bool("VM_LLM_HEALTH_CHECK", True),
            restart_ollama_on_unreachable=_env_bool("VM_LLM_RESTART_ON_FAIL", True),
            fallback_enabled=_env_bool("VM_LLM_FALLBACK_ON_STALL", True),
            progress_interval_sec=max(3.0, _env_float("VM_LLM_PROGRESS_INTERVAL_SEC", 10.0)),
        )


# ── Session state (attempt counters for diagnostics) ─────────────────────

_SESSION_LOCK = threading.RLock()
_session: dict[str, Any] = {
    "attempts": 0,
    "models_tried": [],
    "last_failure": "",
    "last_failure_phase": "",
    "last_ollama": {},
}


def reset_retry_session() -> None:
    with _SESSION_LOCK:
        _session.update(
            {
                "attempts": 0,
                "models_tried": [],
                "last_failure": "",
                "last_failure_phase": "",
                "last_ollama": {},
            }
        )


def retry_session_snapshot() -> dict[str, Any]:
    with _SESSION_LOCK:
        return dict(_session)


def _session_note_attempt(model: str, *, failure: str = "", phase: str = "", ollama: dict | None = None) -> int:
    with _SESSION_LOCK:
        _session["attempts"] = int(_session.get("attempts") or 0) + 1
        tried = list(_session.get("models_tried") or [])
        if model and model not in tried:
            tried.append(model)
        _session["models_tried"] = tried
        if failure:
            _session["last_failure"] = failure
        if phase:
            _session["last_failure_phase"] = phase
        if ollama:
            _session["last_ollama"] = dict(ollama)
        return int(_session["attempts"])


# ── Ollama deep diagnostics ─────────────────────────────────────────────────

_FAILURE_PHASE_LABELS = {
    "ru": {
        "server_down": "сервер Ollama не отвечает",
        "model_missing": "модель не установлена в Ollama",
        "model_cold": "модель не загружена в память (холодный старт)",
        "request_not_sent": "запрос не отправлен (ошибка клиента)",
        "connection_timeout": "таймаут соединения — запрос не дошёл",
        "generation_timeout": "модель приняла запрос, но не завершила генерацию",
        "model_busy": "модель занята другим запросом",
        "out_of_memory": "не хватило памяти (OOM)",
        "api_error": "ошибка API Ollama",
        "unknown": "причина не определена",
    },
    "en": {
        "server_down": "Ollama server not responding",
        "model_missing": "model not installed in Ollama",
        "model_cold": "model not loaded into RAM (cold start)",
        "request_not_sent": "request was not sent (client error)",
        "connection_timeout": "connection timeout — request did not reach server",
        "generation_timeout": "model accepted request but did not finish generation",
        "model_busy": "model busy with another request",
        "out_of_memory": "out of memory (OOM)",
        "api_error": "Ollama API error",
        "unknown": "unknown cause",
    },
}


def failure_phase_label(phase: str, lang: str = "ru") -> str:
    labels = _FAILURE_PHASE_LABELS.get(lang, _FAILURE_PHASE_LABELS["ru"])
    return labels.get(str(phase or "unknown"), phase or "—")


def probe_ollama_detailed(
    model: str,
    *,
    host: str = "127.0.0.1",
    port: int = 11434,
) -> dict[str, Any]:
    """Rich Ollama probe: server, model listed/loaded, failure phase hints."""
    from engines.llm_diagnostics import probe_ollama_health

    base = probe_ollama_health(model, host=host, port=port)
    phase = "unknown"
    if not base.get("server_reachable"):
        phase = "server_down"
    elif not base.get("model_listed"):
        phase = "model_missing"
    elif not base.get("model_loaded"):
        phase = "model_cold"
    elif base.get("status_code") == "busy":
        phase = "model_busy"
    elif base.get("status_code") == "connection_timeout":
        phase = "connection_timeout"
    elif base.get("status_code") == "responding":
        phase = "responding"
    elif base.get("error") and "memory" in str(base.get("error")).lower():
        phase = "out_of_memory"

    base["failure_phase"] = phase
    base["diagnosis_ru"] = failure_phase_label(phase, "ru")
    return base


def classify_call_failure(
    exc: BaseException | None,
    *,
    model_loaded_before: bool,
    ollama_health: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return (failure_code, failure_phase) for a failed HTTP call."""
    err = str(exc or "").lower()
    health = ollama_health or {}

    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read().decode("utf-8", "ignore").lower()
        except Exception:
            pass
        if exc.code == 503 or "memory" in body or "oom" in body or "cuda" in body:
            return "http_error", "out_of_memory"
        return "http_error", "api_error"

    if isinstance(exc, socket.timeout) or "timed out" in err:
        if model_loaded_before:
            return "timeout", "generation_timeout"
        return "timeout", "model_cold" if health.get("model_listed") else "connection_timeout"

    if "connection refused" in err or "actively refused" in err:
        return "connection", "server_down"

    if health.get("status_code") == "unreachable":
        return "unreachable", "server_down"

    return "error", "unknown"


def ensure_ollama_ready(model: str, *, app_dir=None) -> dict[str, Any]:
    """Preflight: probe Ollama; restart service if unreachable (when enabled)."""
    cfg = RetryConfig.from_env(cpu_only=True)
    health = probe_ollama_detailed(model)
    if health.get("server_reachable") or not cfg.restart_ollama_on_unreachable:
        return health

    if health.get("provider") != "ollama" and "11434" not in str(health.get("base_url") or ""):
        return health

    try:
        from pathlib import Path

        root = Path(app_dir) if app_dir else Path(__file__).resolve().parents[1]
        from engines.ai_manager.installer import _ensure_ollama_running

        logger.warning("[LLM Retry] Ollama unreachable — restarting service")
        _ensure_ollama_running(root)
        try:
            from engines.translation_adapt import reset_endpoint_cache

            reset_endpoint_cache()
        except Exception:
            pass
        from engines.llm_adaptation_mode import discover_local_llm

        discover_local_llm(force=True)
        health = probe_ollama_detailed(model)
        health["restarted"] = True
    except Exception as exc:
        health["restart_error"] = str(exc)[:200]
    return health


# ── Fallback model chain ────────────────────────────────────────────────────


def build_fallback_chain(current_model: str, available: list[str] | None = None) -> list[str]:
    """Ordered models to try: current first, then DeepSeek → Qwen → Llama → Gemma."""
    if available is None:
        try:
            from engines.llm_adaptation_mode import detect_capabilities

            available = list(detect_capabilities().get("available_models") or [])
        except Exception:
            available = []

    chain: list[str] = []
    seen: set[str] = set()
    cur = str(current_model or "").strip()
    if cur:
        chain.append(cur)
        seen.add(cur)

    try:
        from engines.llm_providers.registry import FALLBACK_FAMILY_ORDER, get_provider

        for fid in FALLBACK_FAMILY_ORDER:
            prov = get_provider(fid)
            if not prov:
                continue
            alt = prov.resolve_installed_model(available)
            if alt and alt not in seen:
                chain.append(alt)
                seen.add(alt)
    except Exception:
        pass

    for tag in available:
        low = tag.lower()
        if "gemma" in low and tag not in seen:
            chain.append(tag)
            seen.add(tag)

    return chain


# ── Progress notifications ──────────────────────────────────────────────────


def _notify_progress(task_id: str, *, live_message: str, **extra: Any) -> None:
    if not task_id:
        return
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
        from engines.pipeline_progress_tracker import enrich_progress_fields

        fields = enrich_progress_fields(
            task_id,
            phase="translate",
            live_message=live_message,
            **extra,
        )
        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                task.setdefault("info", {}).setdefault("progress_detail", {}).update(fields)
        try:
            from engines.pipeline_watchdog import watchdog_heartbeat

            watchdog_heartbeat(task_id, **fields)
        except Exception:
            pass
    except Exception:
        pass


@dataclass
class _ProgressTicker:
    task_id: str
    message: str
    interval: float
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    started_at: float = field(default_factory=time.time)
    warned_slow: bool = False
    cfg: RetryConfig = field(default_factory=lambda: RetryConfig.from_env())

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="llm-progress-ticker")
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            elapsed = time.time() - self.started_at
            if not self.warned_slow and elapsed >= self.cfg.slow_warn_sec:
                self.warned_slow = True
                _notify_progress(
                    self.task_id,
                    live_message=f"{self.message} — дольше обычного ({int(elapsed)} с)…",
                    llm_slow_warning=True,
                )
            else:
                _notify_progress(
                    self.task_id,
                    live_message=f"{self.message} ({int(elapsed)} с)…",
                )

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)


# ── Retry orchestration ─────────────────────────────────────────────────────


@dataclass
class LLMCallOutcome:
    text: str | None
    ok: bool
    model: str
    provider: str
    attempts: int
    failure: str = ""
    failure_phase: str = ""
    ollama: dict[str, Any] = field(default_factory=dict)
    models_tried: list[str] = field(default_factory=list)


def run_with_retry(
    call_once: Callable[..., tuple[str | None, Exception | None, dict[str, Any]]],
    *,
    prompt: str,
    system: str | None,
    max_tokens: int,
    temperature: float,
    count_budget: bool,
    task_id: str = "",
    segment: int | None = None,
    app_dir=None,
) -> LLMCallOutcome:
    """Execute LLM call with health check, retries, fallback models."""
    if not count_budget:
        cfg = RetryConfig.from_env()
        text, exc, meta = call_once(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            model=None,
            timeout=cfg.call_timeout_sec,
            count_budget=False,
        )
        return LLMCallOutcome(
            text=text,
            ok=bool(text),
            model=str(meta.get("model") or ""),
            provider=str(meta.get("provider") or ""),
            attempts=1 if not exc else 0,
            failure=str(exc) if exc else "",
        )

    try:
        from engines.translation_adapt import _is_cpu_only

        cpu = _is_cpu_only()
    except Exception:
        cpu = True

    try:
        from engines.translation_adapt import _llm_model, _resolve_endpoint

        primary = _llm_model()
        ep = _resolve_endpoint()
        provider = str(ep.get("provider") or "")
        available = list(ep.get("models") or [])
    except Exception:
        primary = os.getenv("VM_TRANSLATE_MODEL", "")
        provider = "ollama"
        available = []

    cfg = RetryConfig.from_env(cpu_only=cpu, model=primary)

    models = build_fallback_chain(primary, available) if cfg.fallback_enabled else [primary]
    if not models:
        models = [primary] if primary else []

    total_attempts = 0
    last_failure = ""
    last_phase = ""
    last_ollama: dict[str, Any] = {}
    models_tried: list[str] = []

    for model_idx, model in enumerate(models):
        if model_idx > 0:
            os.environ["VM_TRANSLATE_MODEL"] = model
            try:
                from engines.translation_adapt import reset_endpoint_cache

                reset_endpoint_cache()
            except Exception:
                pass
            logger.warning("[LLM Retry] Fallback → model %s", model)
            _notify_progress(
                task_id,
                live_message=f"Переключение на резервную модель {model}…",
                llm_model=model,
            )

        models_tried.append(model)
        ollama_before: dict[str, Any] = {}
        if cfg.health_check and provider == "ollama":
            ollama_before = ensure_ollama_ready(model, app_dir=app_dir)
            last_ollama = ollama_before
            if ollama_before.get("failure_phase") == "model_missing":
                last_failure = "model_missing"
                last_phase = "model_missing"
                continue

        model_loaded = bool(ollama_before.get("model_loaded"))

        for attempt in range(cfg.max_retries):
            total_attempts += 1
            attempt_no = attempt + 1
            _session_note_attempt(model, ollama=ollama_before)

            if attempt > 0:
                delay = cfg.retry_delay_sec
                logger.info(
                    "[LLM Retry] model=%s attempt %d/%d after %.0fs delay",
                    model,
                    attempt_no,
                    cfg.max_retries,
                    delay,
                )
                _notify_progress(
                    task_id,
                    live_message=f"Повтор {attempt_no}/{cfg.max_retries} ({model})…",
                    llm_attempt=attempt_no,
                    llm_max_retries=cfg.max_retries,
                    llm_model=model,
                    llm_chars_sent=len(str(prompt or "")),
                    llm_call_timeout_sec=cfg.call_timeout_sec,
                )
                if delay > 0:
                    time.sleep(delay)

            msg = f"Запрос к {model} (попытка {attempt_no}/{cfg.max_retries})"
            ticker = _ProgressTicker(task_id, msg, cfg.progress_interval_sec, cfg=cfg)
            ticker.start()
            try:
                ep_url = ""
                try:
                    from engines.translation_adapt import _resolve_endpoint

                    ep = _resolve_endpoint()
                    ep_url = str(ep.get("url") or ep.get("base_url") or "")
                except Exception:
                    pass
                _notify_progress(
                    task_id,
                    live_message=msg,
                    llm_attempt=attempt_no,
                    llm_max_retries=cfg.max_retries,
                    llm_model=model,
                    llm_chars_sent=len(str(prompt or "")),
                    llm_call_timeout_sec=cfg.call_timeout_sec,
                    llm_api_url=ep_url,
                )
                text, exc, meta = call_once(
                    prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=model,
                    timeout=cfg.call_timeout_sec,
                    count_budget=True,
                    segment=segment,
                    attempt=attempt_no,
                )
            finally:
                ticker.stop()

            if text:
                _session_note_attempt(model)
                return LLMCallOutcome(
                    text=text,
                    ok=True,
                    model=model,
                    provider=str(meta.get("provider") or provider),
                    attempts=total_attempts,
                    models_tried=models_tried,
                    ollama=last_ollama,
                )

            code, phase = classify_call_failure(
                exc,
                model_loaded_before=model_loaded,
                ollama_health=ollama_before,
            )
            last_failure = code
            last_phase = phase
            last_ollama = probe_ollama_detailed(model) if provider == "ollama" else {}
            _session_note_attempt(model, failure=code, phase=phase, ollama=last_ollama)
            logger.warning(
                "[LLM Retry] model=%s attempt %d failed phase=%s err=%s",
                model,
                attempt_no,
                phase,
                exc,
            )
            try:
                from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE
                from engines.translation_stage_log import log_llm_timeout_debug

                if IS_DEBUG_LEARNING_MODE():
                    ep_url = ""
                    try:
                        from engines.translation_adapt import _resolve_endpoint

                        ep = _resolve_endpoint()
                        ep_url = str(ep.get("url") or ep.get("base_url") or "")
                    except Exception:
                        pass
                    log_llm_timeout_debug(
                        app_dir or ".",
                        task_id,
                        source="llm_retry",
                        attempt=attempt_no,
                        wait_sec=round(time.time() - ticker.started_at, 1),
                        llm_call_timeout_sec=cfg.call_timeout_sec,
                        chars_sent=len(str(prompt or "")),
                        segment=segment,
                        model=model,
                        provider=provider,
                        api_url=ep_url,
                        failure_phase=phase,
                        error=str(exc or code),
                    )
            except Exception:
                pass

            if provider == "ollama" and phase in ("server_down", "connection_timeout"):
                last_ollama = ensure_ollama_ready(model, app_dir=app_dir)
                model_loaded = bool(last_ollama.get("model_loaded"))

    return LLMCallOutcome(
        text=None,
        ok=False,
        model=models[-1] if models else primary,
        provider=provider,
        attempts=total_attempts,
        failure=last_failure,
        failure_phase=last_phase,
        ollama=last_ollama,
        models_tried=models_tried,
    )
