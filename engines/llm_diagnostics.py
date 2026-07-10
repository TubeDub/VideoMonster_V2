"""LLM stall diagnostics and recovery helpers (TubeDub translate stage)."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("tubedub.llm_diagnostics")

_PROVIDER_LABELS = {
    "ollama": "Ollama",
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "lmstudio": "LM Studio",
    "openai-compatible": "OpenAI-compatible",
    "anthropic-compatible": "Anthropic-compatible",
    "none": "—",
}

_RECOVERY_LABELS = {
    "ru": {
        "retry_connection": "Повторное подключение к LLM…",
        "restart_llm_server": "Перезапуск LLM-сервера (Ollama)…",
        "switch_fallback_model": "Переключение на резервную модель…",
    },
    "en": {
        "retry_connection": "Retrying LLM connection…",
        "restart_llm_server": "Restarting LLM server (Ollama)…",
        "switch_fallback_model": "Switching to fallback model…",
    },
    "uk": {
        "retry_connection": "Повторне підключення до LLM…",
        "restart_llm_server": "Перезапуск LLM-сервера (Ollama)…",
        "switch_fallback_model": "Перемикання на резервну модель…",
    },
}

_OLLAMA_STATUS_LABELS = {
    "ru": {
        "loaded": "модель загружена",
        "responding": "модель отвечает",
        "busy": "модель занята (очередь запросов)",
        "connection_timeout": "таймаут соединения",
        "api_error": "ошибка API",
        "not_loaded": "модель не загружена в память",
        "unreachable": "сервер Ollama недоступен",
        "unknown": "статус неизвестен",
    },
    "en": {
        "loaded": "model loaded",
        "responding": "model responding",
        "busy": "model busy (request queue)",
        "connection_timeout": "connection timeout",
        "api_error": "API error",
        "not_loaded": "model not loaded into memory",
        "unreachable": "Ollama server unreachable",
        "unknown": "status unknown",
    },
}


def provider_label(provider: str) -> str:
    key = str(provider or "").strip().lower()
    return _PROVIDER_LABELS.get(key, key or "—")


def format_model_display(model: str, *, provider: str = "") -> str:
    """Human-readable model name (DeepSeek-R1, Qwen 2.5, Gemma, GPT-4o, …)."""
    name = str(model or "").strip()
    if not name:
        return "—"
    try:
        from engines.llm_providers.registry import resolve_provider_for_model

        prov = resolve_provider_for_model(name)
        if prov:
            tag = prov.resolve_installed_model([name]) or name
            return f"{prov.display_name} ({tag})"
    except Exception:
        pass
    low = name.lower()
    if "deepseek" in low:
        return f"DeepSeek ({name})"
    if "qwen" in low:
        return f"Qwen ({name})"
    if "gemma" in low:
        return f"Gemma ({name})"
    if "gpt-4" in low or "gpt4" in low:
        return f"GPT-4 ({name})"
    if "llama" in low:
        return f"Llama ({name})"
    return name


def _http_json(url: str, *, timeout: float = 2.0, method: str = "GET", body: bytes | None = None) -> tuple[Any | None, str | None]:
    try:
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore")), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except socket.timeout:
        return None, "connection_timeout"
    except Exception as exc:
        return None, str(exc)[:200]


def probe_ollama_health(model: str, *, host: str = "127.0.0.1", port: int = 11434) -> dict[str, Any]:
    """Check Ollama: listed, loaded in RAM, responding."""
    model = str(model or "").strip()
    base = f"http://{host}:{port}"
    out: dict[str, Any] = {
        "provider": "ollama",
        "base_url": base,
        "model": model,
        "server_reachable": False,
        "model_listed": False,
        "model_loaded": False,
        "responding": False,
        "status": "unknown",
        "status_code": "unknown",
        "detail": "",
        "error": "",
    }
    if not _port_open(host, port, timeout=0.5):
        out["status"] = "unreachable"
        out["status_code"] = "unreachable"
        out["detail"] = "Ollama port closed"
        return out

    out["server_reachable"] = True
    tags, err = _http_json(f"{base}/api/tags", timeout=2.0)
    if err:
        out["status"] = "connection_timeout" if err == "connection_timeout" else "api_error"
        out["status_code"] = out["status"]
        out["error"] = err
        return out

    listed = []
    for m in (tags or {}).get("models") or []:
        n = (m or {}).get("name") or (m or {}).get("model")
        if n:
            listed.append(str(n))
    out["model_listed"] = any(
        model == n or model.split(":")[0] == n.split(":")[0] for n in listed
    )

    ps, _ = _http_json(f"{base}/api/ps", timeout=2.0)
    running = []
    for m in (ps or {}).get("models") or []:
        n = (m or {}).get("name") or (m or {}).get("model")
        if n:
            running.append(str(n))
    out["model_loaded"] = any(
        model == n or model.split(":")[0] == n.split(":")[0] for n in running
    )

    if out["model_loaded"]:
        out["status"] = "loaded"
        out["status_code"] = "loaded"
    elif out["model_listed"]:
        out["status"] = "not_loaded"
        out["status_code"] = "not_loaded"
    else:
        out["status"] = "api_error"
        out["status_code"] = "api_error"
        out["detail"] = "model not in /api/tags"

    if model and out["server_reachable"]:
        ping_body = json.dumps(
            {"model": model, "prompt": "ok", "stream": False, "options": {"num_predict": 1}}
        ).encode("utf-8")
        ping, ping_err = _http_json(
            f"{base}/api/generate",
            timeout=8.0,
            method="POST",
            body=ping_body,
        )
        if ping and (ping.get("response") is not None or ping.get("done")):
            out["responding"] = True
            out["status"] = "responding"
            out["status_code"] = "responding"
        elif ping_err == "connection_timeout":
            if out["model_loaded"]:
                out["status"] = "busy"
                out["status_code"] = "busy"
            else:
                out["status"] = "connection_timeout"
                out["status_code"] = "connection_timeout"
            out["error"] = ping_err
        elif ping_err:
            out["status"] = "api_error"
            out["status_code"] = "api_error"
            out["error"] = ping_err

    return out


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def get_llm_inflight() -> dict[str, Any] | None:
    try:
        from engines.translation_adapt import get_llm_inflight_snapshot

        return get_llm_inflight_snapshot()
    except Exception:
        return None


def collect_llm_stall_context(task_id: str, *, progress_detail: dict | None = None) -> dict[str, Any]:
    """Snapshot for stall UI / reports: model, provider, segment, inflight call."""
    detail = dict(progress_detail or {})
    ui_lang = "ru"
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                ui_lang = str(task.get("ui_lang") or (task.get("info") or {}).get("ui_lang") or "ru")
    except Exception:
        pass
    caps: dict[str, Any] = {}
    try:
        from engines.llm_adaptation_mode import detect_capabilities

        caps = detect_capabilities()
    except Exception:
        pass

    provider = str(detail.get("llm_provider") or caps.get("provider") or "")
    model = str(detail.get("llm_model") or caps.get("model") or "")
    segment = int(detail.get("current_segment") or 0)
    total = int(detail.get("total_segments") or 0)
    attempts = 0
    last_call: dict[str, Any] = {}
    seg_status: dict[str, Any] = {}

    try:
        from engines.translation_adapt import get_llm_calls, get_llm_status, llm_budget_status
        from engines.llm_retry_manager import failure_phase_label, retry_session_snapshot

        calls = get_llm_calls()
        if calls:
            last_call = dict(calls[-1])
        budget = llm_budget_status()
        retry_snap = retry_session_snapshot()
        attempts = int(retry_snap.get("attempts") or 0)
        if segment:
            for row in get_llm_status():
                if int(row.get("segment") or -1) == segment:
                    seg_status = dict(row)
                    attempts = max(attempts, int(row.get("attempts") or 0))
                    break
    except Exception:
        budget = {}
        retry_snap = {}

    inflight = get_llm_inflight() or {}
    if inflight.get("segment") is not None:
        segment = int(inflight.get("segment") or segment or 0)
    chars_sent = int(
        inflight.get("chars_sent")
        or last_call.get("sent_chars")
        or len(str(last_call.get("sent") or ""))
        or detail.get("llm_chars_sent")
        or 0
    )
    wait_sec = 0.0
    if inflight.get("started_at"):
        wait_sec = max(0.0, time.time() - float(inflight["started_at"]))
    elif last_call.get("ms"):
        wait_sec = float(last_call["ms"]) / 1000.0
    elif detail.get("segment_elapsed_sec"):
        wait_sec = float(detail["segment_elapsed_sec"])

    timeout_hit = bool(
        inflight.get("timed_out")
        or last_call.get("finish_reason") == "error"
        or seg_status.get("skip_reason") in ("llm_semaphore_timeout", "segment_time_budget")
    )

    ollama_health: dict[str, Any] | None = None
    failure_phase = str(retry_snap.get("last_failure_phase") or seg_status.get("skip_reason") or "")
    if provider == "ollama" and model:
        ollama_health = probe_ollama_detailed(model)
        if not failure_phase:
            failure_phase = str(ollama_health.get("failure_phase") or ollama_health.get("status_code") or "")

    return {
        "task_id": task_id,
        "provider": provider,
        "provider_label": provider_label(provider),
        "model": model,
        "model_display": format_model_display(model, provider=provider),
        "segment": segment,
        "total_segments": total,
        "segment_label": f"{segment} / {total}" if segment and total else (str(segment) if segment else ""),
        "chars_sent": chars_sent,
        "wait_sec": round(wait_sec, 1),
        "attempts": max(attempts, int(seg_status.get("attempts") or 0)),
        "timeout": timeout_hit,
        "failure_phase": failure_phase,
        "failure_phase_label": failure_phase_label(failure_phase, ui_lang) if failure_phase else "",
        "models_tried": list(retry_snap.get("models_tried") or []),
        "inflight": bool(inflight),
        "last_call": last_call,
        "segment_status": seg_status,
        "budget": budget,
        "ollama": ollama_health,
        "available_models": caps.get("available_models") or [],
    }


def ollama_status_text(status_code: str, lang: str = "ru") -> str:
    labels = _OLLAMA_STATUS_LABELS.get(lang, _OLLAMA_STATUS_LABELS["ru"])
    return labels.get(str(status_code or "unknown"), status_code or "—")


def build_llm_stall_message(ctx: dict[str, Any], *, idle_sec: float, lang: str = "ru") -> str:
    """Detailed user-facing stall reason for translate stage."""
    model_disp = ctx.get("model_display") or ctx.get("model") or "—"
    provider = ctx.get("provider_label") or ctx.get("provider") or "—"
    seg = ctx.get("segment") or 0
    total = ctx.get("total_segments") or 0
    chars = ctx.get("chars_sent") or 0
    wait = ctx.get("wait_sec") or idle_sec
    attempts = ctx.get("attempts") or 0
    timed_out = ctx.get("timeout")

    mins = int(wait // 60)
    sec = int(wait % 60)
    wait_str = f"{mins} мин {sec} с" if lang != "en" else f"{mins}m {sec}s"

    seg_part = ""
    if seg and total:
        if lang == "en":
            seg_part = f"Segment #{seg} of {total}. "
        elif lang == "uk":
            seg_part = f"Сегмент №{seg} з {total}. "
        else:
            seg_part = f"Сегмент №{seg} из {total}. "
    elif seg:
        seg_part = f"Сегмент №{seg}. " if lang != "en" else f"Segment #{seg}. "

    ollama = ctx.get("ollama") or {}
    phase = ctx.get("failure_phase_label") or ctx.get("failure_phase") or ""
    ollama_line = ""
    if ctx.get("provider") == "ollama":
        if phase:
            ollama_line = f" Ollama: {phase}."
        elif ollama:
            from engines.llm_diagnostics import ollama_status_text

            st = ollama_status_text(str(ollama.get("status_code") or ""), lang)
            ollama_line = f" Ollama: {st}."

    timeout_word = "да" if timed_out else "нет"
    if lang == "en":
        timeout_word = "yes" if timed_out else "no"
    elif lang == "uk":
        timeout_word = "так" if timed_out else "ні"

    if lang == "en":
        return (
            f"Translation stalled.{seg_part}"
            f"Model: {model_disp}. Provider: {provider}. "
            f"Sent {chars} chars, waited {wait_str}, attempts: {attempts}, timeout: {timeout_word}."
            f"{ollama_line}"
        )
    if lang == "uk":
        return (
            f"Етап перекладу завис.{seg_part}"
            f"Модель: {model_disp}. Провайдер: {provider}. "
            f"Надіслано {chars} симв., очікування {wait_str}, спроб: {attempts}, таймаут: {timeout_word}."
            f"{ollama_line}"
        )
    return (
        f"Этап перевода завис.{seg_part}"
        f"Модель: {model_disp}. Провайдер: {provider}. "
        f"Отправлено {chars} симв., ожидание {wait_str}, попыток: {attempts}, таймаут: {timeout_word}."
        f"{ollama_line}"
    )


def _fallback_enabled() -> bool:
    raw = str(os.getenv("VM_LLM_FALLBACK_ON_STALL") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def attempt_llm_recovery(
    task_id: str,
    *,
    attempt_index: int,
    app_dir=None,
) -> dict[str, Any]:
    """Recovery ladder: reconnect → restart Ollama → fallback model."""
    from pathlib import Path

    root = Path(app_dir) if app_dir else Path(__file__).resolve().parents[1]
    steps = ("retry_connection", "restart_llm_server", "switch_fallback_model")
    step = steps[min(attempt_index, len(steps) - 1)]
    result: dict[str, Any] = {"step": step, "ok": False, "detail": ""}

    try:
        from engines.translation_adapt import reset_endpoint_cache

        reset_endpoint_cache()
    except Exception:
        pass

    if step == "retry_connection":
        try:
            from engines.llm_adaptation_mode import discover_local_llm

            ep = discover_local_llm(force=True)
            result["ok"] = bool(ep)
            result["detail"] = str((ep or {}).get("base_url") or "no endpoint")
        except Exception as exc:
            result["detail"] = str(exc)
        return result

    if step == "restart_llm_server":
        try:
            from engines.llm_adaptation_mode import detect_capabilities

            caps = detect_capabilities()
            if caps.get("provider") != "ollama":
                result["ok"] = True
                result["detail"] = "not ollama — skipped"
                return result
            from engines.ai_manager.installer import _ensure_ollama_running

            _ensure_ollama_running(root)
            from engines.llm_adaptation_mode import discover_local_llm

            ep = discover_local_llm(force=True)
            result["ok"] = bool(ep)
            result["detail"] = "ollama restarted"
        except Exception as exc:
            result["detail"] = str(exc)
        return result

    if step == "switch_fallback_model":
        if not _fallback_enabled():
            result["detail"] = "fallback disabled"
            return result
        try:
            from engines.llm_adaptation_mode import detect_capabilities

            caps = detect_capabilities()
            current = str(caps.get("model") or "")
            available = list(caps.get("available_models") or [])
            from engines.llm_providers.registry import FALLBACK_FAMILY_ORDER, get_provider

            for fid in FALLBACK_FAMILY_ORDER:
                prov = get_provider(fid)
                if not prov:
                    continue
                alt = prov.resolve_installed_model(available)
                if alt and alt != current:
                    os.environ["VM_TRANSLATE_MODEL"] = alt
                    try:
                        from engines.translation_adapt import reset_endpoint_cache

                        reset_endpoint_cache()
                    except Exception:
                        pass
                    result["ok"] = True
                    result["detail"] = alt
                    result["model"] = alt
                    logger.warning(
                        "Task %s: LLM recovery switched model %s → %s",
                        task_id,
                        current,
                        alt,
                    )
                    return result
            result["detail"] = "no alternate model installed"
        except Exception as exc:
            result["detail"] = str(exc)
        return result

    return result


def recovery_live_message(step: str, lang: str = "ru") -> str:
    labels = _RECOVERY_LABELS.get(lang, _RECOVERY_LABELS["ru"])
    return labels.get(step, step)
