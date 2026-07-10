"""
Адаптивное сокращение перевода под тайминг (FINAL TZ №2).
Приоритет: смысл → естественность → тайминг → близость к оригиналу.
Скорость TTS меняется только после исчерпания адаптации текста.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

logger = logging.getLogger("tubedub.engines.translation_adapt")

# ── LLM per-segment time budget (P0 rewrite) ───────────────────────────────
# A local LLM on a CPU-only machine can take 1–3 minutes per call. The OLD
# design used ONE shared time budget for the whole dub run — so once the early
# segments spent it, every later segment was skipped with `budget_exhausted`
# and got LLM_NOT_CALLED. That made quality depend on video length.
#
# NEW design (per ТЗ P0):
#   • NO single project-wide time budget may skip LLM for a segment.
#   • Each segment has its OWN independent time budget (e.g. 60s). A hard
#     segment never steals time from the following ones.
#   • Every segment is GUARANTEED at least one LLM attempt; the per-segment
#     budget only limits the number of EXTRA refinement rounds, and never
#     produces a `budget_exhausted` LLM_NOT_CALLED.
#   • The optional project budget is telemetry / soft-cap only — it never
#     blocks a segment's first call.
#   • Speed modes: fast / balanced / max_quality (max_quality ≈ unlimited).
_LLM_BUDGET_LOCK = threading.Lock()
_llm_budget: dict = {
    "task": None,        # current dub run id
    "spent_s": 0.0,      # cumulative LLM wall time this run (telemetry)
    "consec_fail": 0,    # legacy telemetry
    "open": False,       # kept for backward-compat; never blocks a first call
    "logged_open": False,
    # ── Global (project-wide) LLM circuit breaker (P0 no-hang) ──────────────
    # When the local LLM proves hopeless for a whole run (many CONSECUTIVE hard
    # failures: empty replies, timeouts, transport errors, token-truncation),
    # we stop calling it for the rest of the run and let every remaining segment
    # fall back to rule-based / kept text instead of each one waiting on a slow
    # or broken model. This is separate from the per-segment breaker so the two
    # never interfere. Reset on every new dub run (begin_llm_run).
    "global_consec_fail": 0,
    "global_open": False,
    "global_logged": False,
    # ── "Model too slow / unusable" hard breaker (survives phase resets) ─────
    # A local model that times out or errors on its calls will NOT get faster in
    # a later phase, so this breaker is deliberately sticky: once tripped, the
    # whole run stops calling the LLM and falls back to MT/rule output. This is
    # what keeps a 20-minute doomed run (every call hitting the 90s timeout) from
    # happening — the dub finishes fast with the (decent) base translation.
    "slow_fail": 0,
    "model_too_slow": False,
    "slow_logged": False,
}
# Per-segment state: breaker (consecutive failures), spent time, attempts.
_seg_breakers: dict[int, dict] = {}

# A call whose wall time reaches this fraction of its timeout (and failed) is
# treated as a "slow/timeout" failure for the sticky breaker.
_SLOW_CALL_TIMEOUT_FRAC = 0.85
# Open the sticky breaker after this many slow/timeout failures.
_MAX_SLOW_FAILS_DEFAULT = 2

# Speed / quality modes.
MODE_FAST = "fast"
MODE_BALANCED = "balanced"
MODE_MAX_QUALITY = "max_quality"

# Per-segment budget (seconds) per mode. 0 == unlimited.
_MODE_SEGMENT_BUDGET_S: dict[str, float] = {
    MODE_FAST: 20.0,
    MODE_BALANCED: 60.0,
    MODE_MAX_QUALITY: 0.0,  # unlimited — quality first
}
# Optional soft project budget (seconds) per mode. 0 == unlimited. This is a
# telemetry / soft ceiling only and NEVER causes a segment to be skipped.
_MODE_PROJECT_BUDGET_S: dict[str, float] = {
    MODE_FAST: 0.0,
    MODE_BALANCED: 0.0,
    MODE_MAX_QUALITY: 0.0,
}

# Runtime adaptation-budget configuration (set per dub run via
# configure_adaptation_budget / begin_llm_run, or from env). Explicit values
# (> 0) win over the mode defaults.
_adapt_budget_cfg: dict = {
    "mode": MODE_BALANCED,
    "per_segment_s": 0.0,   # explicit override; 0 → resolve from mode
    "project_s": 0.0,       # explicit override; 0 → resolve from mode
}


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.getenv(name, "") or "")
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "") or "")
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _is_cpu_only() -> bool:
    try:
        from engines.llm_adaptation_mode import _has_gpu

        return not _has_gpu()
    except Exception:
        return True


def _llm_call_timeout() -> float:
    """Per-call socket timeout (Retry Manager may override per attempt)."""
    try:
        from engines.llm_retry_manager import RetryConfig

        model = _llm_model()
        return RetryConfig.from_env(cpu_only=_is_cpu_only(), model=model).call_timeout_sec
    except Exception:
        default = 45.0 if _is_cpu_only() else 15.0
        return _env_float("VM_LLM_CALL_TIMEOUT", default)


def agent_llm_timeout(default_gpu: float = 15.0) -> float:
    """Timeout for ai_core agent LLM calls — must match transport on CPU."""
    if _is_cpu_only():
        try:
            from engines.llm_adaptation_mode import _model_param_billions

            param_b = _model_param_billions(_llm_model())
            if param_b >= 13:
                return _env_float("VM_LLM_AGENT_TIMEOUT", 180.0)
            if 0.0 < param_b < 5.0:
                return _env_float("VM_LLM_AGENT_TIMEOUT", 90.0)
        except Exception:
            pass
        return _env_float("VM_LLM_AGENT_TIMEOUT", _env_float("VM_LLM_CALL_TIMEOUT", 120.0))
    return _env_float("VM_LLM_AGENT_TIMEOUT", default_gpu)


def _max_slow_fails() -> int:
    """Slow-failure threshold before the sticky breaker opens."""
    default = 5 if _is_cpu_only() else _MAX_SLOW_FAILS_DEFAULT
    return _env_int("VM_LLM_MAX_SLOW_FAILS", default)


def normalize_speed_mode(value) -> str:
    """Map any user/env value to one of the three canonical speed modes."""
    v = str(value or "").strip().lower()
    if v in (MODE_FAST, "быстро", "quick", "speed"):
        return MODE_FAST
    if v in (MODE_MAX_QUALITY, "max_quality", "quality", "max", "best",
             "максимальное качество", "максимальное_качество", "максимум"):
        return MODE_MAX_QUALITY
    if v in (MODE_BALANCED, "balance", "balanced", "баланс"):
        return MODE_BALANCED
    return MODE_BALANCED


def configure_adaptation_budget(
    *,
    mode: str | None = None,
    per_segment_s: float | None = None,
    project_s: float | None = None,
) -> None:
    """Configure the adaptation budget for the current dub run (ТЗ §3).

    ``mode`` is one of fast / balanced / max_quality. ``per_segment_s`` and
    ``project_s`` are optional explicit overrides (seconds); 0 or None means
    "resolve from the mode".
    """
    with _LLM_BUDGET_LOCK:
        if mode is not None:
            _adapt_budget_cfg["mode"] = normalize_speed_mode(mode)
        if per_segment_s is not None:
            _adapt_budget_cfg["per_segment_s"] = max(0.0, float(per_segment_s))
        if project_s is not None:
            _adapt_budget_cfg["project_s"] = max(0.0, float(project_s))


def adaptation_speed_mode() -> str:
    """Resolve the active speed mode: env → runtime config → balanced."""
    env_mode = os.getenv("VM_ADAPTATION_SPEED_MODE") or os.getenv("VM_ADAPT_MODE")
    if env_mode:
        return normalize_speed_mode(env_mode)
    with _LLM_BUDGET_LOCK:
        return normalize_speed_mode(_adapt_budget_cfg.get("mode"))


def per_segment_budget_s() -> float:
    """Independent time budget for ONE segment (seconds). 0 == unlimited.

    Priority: env override → explicit runtime override → mode default. CPU-only
    machines get a longer floor so a cold model still fits its first call.
    """
    env_v = _env_float("VM_LLM_SEGMENT_BUDGET_S", 0.0)
    if env_v > 0:
        return env_v
    with _LLM_BUDGET_LOCK:
        explicit = float(_adapt_budget_cfg.get("per_segment_s") or 0.0)
    mode = adaptation_speed_mode()
    if mode == MODE_MAX_QUALITY:
        return 0.0  # unlimited — quality first, never time-skip
    budget = explicit if explicit > 0 else _MODE_SEGMENT_BUDGET_S.get(mode, 60.0)
    if budget <= 0:
        return 0.0
    if _is_cpu_only():
        # A single cold CPU call may take ~90s; never let the budget cut the
        # very first attempt short.
        budget = max(budget, _llm_call_timeout() + 30.0)
    return budget


def project_budget_s() -> float:
    """Soft project-wide ceiling (seconds). 0 == unlimited. Telemetry only —
    it never skips a segment's LLM call (ТЗ §1/§4)."""
    env_v = _env_float("VM_LLM_PROJECT_BUDGET_S", 0.0)
    if env_v > 0:
        return env_v
    with _LLM_BUDGET_LOCK:
        explicit = float(_adapt_budget_cfg.get("project_s") or 0.0)
    if explicit > 0:
        return explicit
    return _MODE_PROJECT_BUDGET_S.get(adaptation_speed_mode(), 0.0)


def _llm_max_consec_fail() -> int:
    """Consecutive failures allowed **per segment** before that segment's breaker opens."""
    default = 5 if _is_cpu_only() else 3
    return _env_int("VM_LLM_MAX_CONSEC_FAIL", default)


def _llm_global_max_consec_fail() -> int:
    """Consecutive hard failures across the WHOLE run before the global breaker
    opens (P0 no-hang). Deliberately larger than the per-segment threshold so a
    single hard segment never trips it — it fires only when the local LLM is
    hopeless for the whole run, at which point we stop waiting on it everywhere.
    """
    return _env_int("VM_LLM_GLOBAL_MAX_CONSEC_FAIL", 8)


def _bump_global_failure_locked(reason: str = "") -> None:
    """Increment the global consecutive-failure count and open the circuit
    breaker once the threshold is crossed. Caller MUST hold _LLM_BUDGET_LOCK.
    """
    _llm_budget["global_consec_fail"] = int(
        _llm_budget.get("global_consec_fail") or 0
    ) + 1
    if (
        _llm_budget["global_consec_fail"] >= _llm_global_max_consec_fail()
        and not _llm_budget.get("global_open")
    ):
        _llm_budget["global_open"] = True
        if not _llm_budget.get("global_logged"):
            _llm_budget["global_logged"] = True
            logger.warning(
                "[Adapt] GLOBAL LLM circuit breaker OPEN after %d consecutive "
                "failures (%s) — stopping LLM calls for the rest of this run; "
                "segments fall back to rule-based / kept text (no hang).",
                _llm_budget["global_consec_fail"],
                reason or "hard_failures",
            )


def record_llm_unusable(reason: str = "no_usable_output") -> None:
    """Signal that an LLM call ran but produced NOTHING usable downstream.

    ``_llm_chat`` only sees the raw HTTP reply, so a non-empty response that
    every downstream consumer rejects (0 parseable rephrase variants, all
    variants failing validation, a stalled/timed-out adaptation segment) would
    otherwise reset the breaker and let every remaining segment keep paying the
    full slow-LLM cost. Callers report those cases here so repeated useless
    output still trips the global circuit breaker (P0: bound N×slow segments).
    """
    with _LLM_BUDGET_LOCK:
        _bump_global_failure_locked(reason)


def _note_slow_failure_locked(elapsed_s: float, timeout_s: float) -> None:
    """Trip the sticky 'model too slow' breaker after repeated timeouts.

    Caller MUST hold _LLM_BUDGET_LOCK. A failure whose wall time reached most of
    the allotted timeout is a timeout/too-slow signal (not a quick transport
    error), so we count it toward the sticky breaker that survives phase resets.
    """
    if timeout_s <= 0:
        return
    if elapsed_s < timeout_s * _SLOW_CALL_TIMEOUT_FRAC:
        return
    _llm_budget["slow_fail"] = int(_llm_budget.get("slow_fail") or 0) + 1
    if _llm_budget["slow_fail"] >= _max_slow_fails() and not _llm_budget.get("model_too_slow"):
        _llm_budget["model_too_slow"] = True
        _llm_budget["global_open"] = True
        if not _llm_budget.get("slow_logged"):
            _llm_budget["slow_logged"] = True
            logger.warning(
                "[Adapt] LLM model is TOO SLOW (%d calls hit ~%.0fs timeout) — "
                "disabling LLM adaptation for the rest of this run so the dub "
                "finishes fast on the base translation. Use a faster/smaller "
                "model or a GPU/cloud endpoint for intelligent adaptation.",
                _llm_budget["slow_fail"],
                timeout_s,
            )


def circuit_open() -> bool:
    """True when the global LLM circuit breaker has tripped for this run.

    While open, no more real LLM calls are made (cache hits are still served);
    every caller falls back to rule-based / kept text so the run cannot hang on
    a broken or unbearably slow local model. Includes the sticky "model too
    slow" breaker which persists across pipeline-phase resets.
    """
    with _LLM_BUDGET_LOCK:
        return bool(_llm_budget.get("global_open") or _llm_budget.get("model_too_slow"))


def reset_circuit_breaker() -> None:
    """Manually reset the global LLM circuit breaker (mainly for tests)."""
    with _LLM_BUDGET_LOCK:
        _llm_budget["global_consec_fail"] = 0
        _llm_budget["global_open"] = False
        _llm_budget["global_logged"] = False
        _llm_budget["slow_fail"] = 0
        _llm_budget["model_too_slow"] = False
        _llm_budget["slow_logged"] = False


def reset_circuit_for_phase(phase: str) -> None:
    """Reset global circuit at pipeline phase boundary.

    Translation-phase LLM failures must not block POST_TTS_QA timing adaptation.
    Per-segment breakers are preserved. The sticky "model too slow" breaker is
    NOT reset — a model that timed out earlier will not be fast now, and retrying
    it every phase is exactly what caused 20-minute doomed runs.
    """
    with _LLM_BUDGET_LOCK:
        if _llm_budget.get("model_too_slow"):
            logger.info(
                "[Adapt] phase=%s: LLM stays disabled (model_too_slow sticky breaker)",
                phase,
            )
            _llm_budget["phase"] = str(phase or "")
            return
        _llm_budget["global_consec_fail"] = 0
        _llm_budget["global_open"] = False
        _llm_budget["global_logged"] = False
        _llm_budget["phase"] = str(phase or "")
    logger.info("[Adapt] global circuit breaker reset for phase=%s", phase)


def _new_seg_row() -> dict:
    return {"consec_fail": 0, "open": False, "spent_s": 0.0, "attempts": 0}


def begin_llm_run(
    task_id: str | None,
    *,
    mode: str | None = None,
    per_segment_s: float | None = None,
    project_s: float | None = None,
) -> None:
    """Start (or continue) LLM adaptation for a dub run.

    Resets the per-segment state only when a *new* task id is seen. Optional
    ``mode`` / ``per_segment_s`` / ``project_s`` configure the budget for this
    run (ТЗ §3). Safe to call repeatedly.
    """
    if mode is not None or per_segment_s is not None or project_s is not None:
        configure_adaptation_budget(
            mode=mode, per_segment_s=per_segment_s, project_s=project_s
        )
    elif _is_cpu_only() and not (
        os.getenv("VM_ADAPTATION_SPEED_MODE") or os.getenv("VM_ADAPT_MODE")
    ):
        try:
            from engines.llm_providers.registry import load_quality_mode

            configure_adaptation_budget(mode=load_quality_mode())
        except Exception:
            configure_adaptation_budget(mode=MODE_MAX_QUALITY)
    tid = str(task_id or "")
    with _LLM_BUDGET_LOCK:
        if _llm_budget["task"] == tid and tid:
            return
        _llm_budget.update(
            {
                "task": tid, "spent_s": 0.0, "consec_fail": 0, "open": False,
                "logged_open": False, "global_consec_fail": 0, "global_open": False,
                "global_logged": False, "slow_fail": 0, "model_too_slow": False,
                "slow_logged": False,
            }
        )
        _seg_breakers.clear()
    # New dub run → start a fresh LLM call capture for the effectiveness report.
    begin_llm_capture()


def reset_llm_budget() -> None:
    with _LLM_BUDGET_LOCK:
        _llm_budget.update(
            {
                "task": None, "spent_s": 0.0, "consec_fail": 0, "open": False,
                "logged_open": False, "global_consec_fail": 0, "global_open": False,
                "global_logged": False, "slow_fail": 0, "model_too_slow": False,
                "slow_logged": False,
            }
        )
        _seg_breakers.clear()


def reset_segment_llm_breaker(segment: int | None) -> None:
    """Reset a segment's independent budget + breaker (fresh, full budget)."""
    if segment is None:
        return
    with _LLM_BUDGET_LOCK:
        _seg_breakers[int(segment)] = _new_seg_row()


def _segment_breaker_open(segment: int | None) -> bool:
    if segment is None:
        return False
    with _LLM_BUDGET_LOCK:
        return bool(_seg_breakers.get(int(segment), {}).get("open"))


def _segment_time_budget_open(segment: int | None) -> bool:
    """True when a segment has used up its OWN time budget.

    Guarantees at least one attempt: returns False until the segment has made
    ≥1 attempt. Never affects other segments. Unlimited when budget == 0
    (max_quality). This NEVER yields an LLM_NOT_CALLED because the first call
    is always allowed (which sets called=True).
    """
    if segment is None:
        return False
    budget = per_segment_budget_s()
    if budget <= 0:
        return False
    with _LLM_BUDGET_LOCK:
        row = _seg_breakers.get(int(segment))
        if not row:
            return False
        if int(row.get("attempts") or 0) < 1:
            return False  # never skip the first attempt
        return float(row.get("spent_s") or 0.0) >= budget


def _llm_breaker_open() -> bool:
    """Backward-compat shim. The project budget is SOFT and never blocks a
    segment's LLM call (ТЗ §1). Kept only so old callers/telemetry keep working.
    Returns True only if the soft project ceiling was crossed — callers must
    NOT use this to skip a segment."""
    budget = project_budget_s()
    if budget <= 0:
        return False
    with _LLM_BUDGET_LOCK:
        crossed = _llm_budget["spent_s"] >= budget
        if crossed and not _llm_budget["logged_open"]:
            logger.info(
                "[Adapt] soft project budget reached (%.0fs / %.0fs) — "
                "continuing per-segment (segments are NEVER skipped).",
                _llm_budget["spent_s"],
                budget,
            )
            _llm_budget["logged_open"] = True
        return crossed


def _llm_record(dt: float, ok: bool, *, segment: int | None = None) -> None:
    seg = segment if segment is not None else _llm_ctx.get("segment")
    with _LLM_BUDGET_LOCK:
        _llm_budget["spent_s"] += max(0.0, dt)
        if seg is not None:
            row = _seg_breakers.setdefault(int(seg), _new_seg_row())
            row["spent_s"] = float(row.get("spent_s") or 0.0) + max(0.0, dt)
            row["attempts"] = int(row.get("attempts") or 0) + 1
        if ok:
            _llm_budget["consec_fail"] = 0
            _llm_budget["global_consec_fail"] = 0
            if seg is not None:
                row = _seg_breakers[int(seg)]
                row["consec_fail"] = 0
                row["open"] = False
        else:
            _llm_budget["consec_fail"] += 1
            _bump_global_failure_locked("llm_call_failed")
            if seg is not None:
                row = _seg_breakers[int(seg)]
                row["consec_fail"] = int(row.get("consec_fail") or 0) + 1
                if row["consec_fail"] >= _llm_max_consec_fail() and not row["open"]:
                    row["open"] = True
                    logger.warning(
                        "[Adapt] segment %s LLM unresponsive (%d consecutive failures) — "
                        "skipping further LLM calls for this segment only.",
                        seg,
                        row["consec_fail"],
                    )


def llm_budget_status() -> dict:
    with _LLM_BUDGET_LOCK:
        status = dict(_llm_budget)
    status["mode"] = adaptation_speed_mode()
    status["per_segment_budget_s"] = per_segment_budget_s()
    status["project_budget_s"] = project_budget_s()
    status["circuit_open"] = bool(status.get("global_open"))
    return status


# ── LLM call recorder (AutoDub audit TЗ §1/§2/§9) ──────────────────────────
# Proof that the LLM ran: every real adaptation call is captured with the text
# sent, the text received, finish_reason, latency and whether the output was
# usable. The active dub sets the current segment via set_llm_context so the
# OpenDDF report can attribute each call to a segment.
_LLM_CALLS_LOCK = threading.Lock()
_llm_calls: list[dict] = []


class _ThreadLocalCtx:
    """Thread-local LLM context (segment/stage).

    Parallel segment adaptation (Task 5) runs several segments on different
    threads at once. A single shared dict would cross-attribute LLM calls to the
    wrong segment and corrupt the per-segment budget context. Making the context
    thread-local keeps every worker's attribution correct while the shared call
    log / status dicts stay guarded by ``_LLM_CALLS_LOCK``.
    """

    def __init__(self) -> None:
        self._tl = threading.local()

    def _d(self) -> dict:
        d = getattr(self._tl, "d", None)
        if d is None:
            d = {"segment": None, "stage": None}
            self._tl.d = d
        return d

    def get(self, key, default=None):
        return self._d().get(key, default)

    def update(self, other: dict) -> None:
        self._d().update(other)

    def __getitem__(self, key):
        return self._d()[key]

    def __setitem__(self, key, value) -> None:
        self._d()[key] = value


_llm_ctx = _ThreadLocalCtx()

# LLM network concurrency (Task 5/6). A single local model is served best by one
# request at a time; segments needing the LLM QUEUE on this semaphore instead of
# being skipped, while non-LLM (rule-only) segments proceed fully in parallel.
_LLM_SEM_LOCK = threading.Lock()
_llm_semaphore: threading.Semaphore | None = None
_llm_semaphore_size = 0


def _get_llm_semaphore() -> threading.Semaphore:
    global _llm_semaphore, _llm_semaphore_size
    size = _env_int("VM_LLM_MAX_CONCURRENCY", 1)
    with _LLM_SEM_LOCK:
        if _llm_semaphore is None or size != _llm_semaphore_size:
            _llm_semaphore = threading.Semaphore(max(1, size))
            _llm_semaphore_size = max(1, size)
        return _llm_semaphore
# Per-segment LLM decision status (audit P0 §1/§2/§4). Guarantees no silent
# skip: every segment that reaches the LLM stage is recorded as called,
# skipped (with reason), or no-rewrite (with reason).
_llm_status: dict[int, dict] = {}
_llm_inflight: dict[str, Any] | None = None


def get_llm_inflight_snapshot() -> dict[str, Any] | None:
    with _LLM_CALLS_LOCK:
        return dict(_llm_inflight) if _llm_inflight else None


def _set_llm_inflight(**fields: Any) -> None:
    global _llm_inflight
    with _LLM_CALLS_LOCK:
        if fields:
            base = dict(_llm_inflight or {})
            base.update(fields)
            _llm_inflight = base
        else:
            _llm_inflight = None


def begin_llm_capture() -> None:
    with _LLM_CALLS_LOCK:
        _llm_calls.clear()
        _llm_status.clear()
        _llm_ctx.update({"segment": None, "stage": None})
        global _llm_inflight
        _llm_inflight = None
    try:
        from engines.llm_retry_manager import reset_retry_session

        reset_retry_session()
    except Exception:
        pass


def set_llm_context(*, segment: int | None = None, stage: str | None = None) -> None:
    with _LLM_CALLS_LOCK:
        if segment is not None:
            _llm_ctx["segment"] = segment
        if stage is not None:
            _llm_ctx["stage"] = stage


def _seg_status(seg) -> dict:
    """Get/create the status row for a segment (caller holds the lock)."""
    key = seg if seg is not None else -1
    row = _llm_status.get(key)
    if row is None:
        row = {
            "segment": seg,
            "needed": False,      # reached the LLM stage (adaptation required)
            "called": False,      # a real network/cached LLM call happened
            "attempts": 0,        # LLM calls attempted for this segment
            "skip_reason": None,  # why LLM was NOT called (no_endpoint/breaker/…)
            "no_rewrite": False,  # LLM returned identical text (fictitious work)
            "no_rewrite_reason": None,
        }
        _llm_status[key] = row
    return row


def mark_llm_needed(segment: int | None = None) -> None:
    """Flag that a segment genuinely requires LLM adaptation (audit §1)."""
    with _LLM_CALLS_LOCK:
        seg = segment if segment is not None else _llm_ctx.get("segment")
        _seg_status(seg)["needed"] = True


def record_llm_skip(reason: str) -> None:
    """Record that the LLM was NOT called for the current segment (audit §2).

    Never silent: `reason` is one of no_endpoint | disabled | error |
    segment_breaker_open | segment_time_budget. The per-segment guards
    (segment_*) only fire AFTER a segment already made its first call, so they
    never turn into an LLM_NOT_CALLED (ТЗ P0: no budget_exhausted skip).
    """
    with _LLM_CALLS_LOCK:
        row = _seg_status(_llm_ctx.get("segment"))
        row["needed"] = True
        if not row["called"]:
            row["skip_reason"] = reason


def record_llm_no_rewrite(reason: str = "identical_output") -> None:
    """Record that the LLM ran but returned identical text (audit §4)."""
    with _LLM_CALLS_LOCK:
        row = _seg_status(_llm_ctx.get("segment"))
        row["no_rewrite"] = True
        row["no_rewrite_reason"] = reason


def get_llm_status() -> list[dict]:
    with _LLM_CALLS_LOCK:
        return [dict(v) for v in _llm_status.values()]


def _record_llm_call(
    sent: str,
    received: str,
    *,
    finish_reason: str,
    ms: float,
    ok: bool,
    purpose: str = "",
    provider: str = "",
    model: str = "",
) -> None:
    with _LLM_CALLS_LOCK:
        seg = _llm_ctx.get("segment")
        _llm_calls.append(
            {
                "segment": seg,
                "stage": _llm_ctx.get("stage"),
                "purpose": purpose,
                "provider": provider,
                "model": model,
                "sent": str(sent or "")[:500],
                "sent_chars": len(str(sent or "")),
                "received": str(received or "")[:500],
                "finish_reason": finish_reason,
                "ms": round(float(ms), 1),
                "usable": bool(ok),
            }
        )
        row = _seg_status(seg)
        row["needed"] = True
        row["called"] = True
        row["attempts"] += 1
        row["skip_reason"] = None


def get_llm_calls() -> list[dict]:
    with _LLM_CALLS_LOCK:
        return [dict(c) for c in _llm_calls]


def drain_llm_calls() -> list[dict]:
    with _LLM_CALLS_LOCK:
        calls = [dict(c) for c in _llm_calls]
        _llm_calls.clear()
        return calls

_FILLERS_UNIVERSAL = re.compile(
    r"\b("
    r"like|you know|basically|actually|literally|just|really|kind of|sort of|"
    r"well|so|okay|ok|"
    r"как бы|типа|ну|в общем|собственно|конечно же|просто|действительно|"
    r"буквально|в принципе|на самом деле|кстати|"
    r"tipo|o sea|en realidad|básicamente|"
    r"eigentlich|sozusagen|quasi|"
    r"en fait|bon|quoi|"
    r"praticamente|diciamo|"
    r"właściwie|w sumie"
    r")\b",
    re.IGNORECASE,
)
_SOFTENERS_UNIVERSAL = re.compile(
    r"\b("
    r"very|really|quite|rather|somewhat|fairly|extremely|"
    r"очень|слишком|довольно|достаточно|немного|весьма|"
    r"dużo|bardzo|"
    r"sehr|ziemlich|"
    r"très|vraiment|"
    r"muy|realmente"
    r")\s+",
    re.IGNORECASE,
)

# Длинные конструкции → короткие (универсальный набор + RU)
_SHORTEN_PATTERNS: list[tuple[str, str]] = [
    (r"\bin order to\b", "to"),
    (r"\bat the present time\b", "now"),
    (r"\bat this moment\b", "now"),
    (r"\bit is necessary to\b", "must"),
    (r"\bit should be noted\b", ""),
    (r"\bI think that\b", "I think"),
    (r"\bI believe that\b", "I believe"),
    (r"\bhowever,\s*", "but "),
    (r"\bnevertheless,\s*", "but "),
    (r"\bin addition,\s*", "also "),
    (r"\bв настоящее время\b", "сейчас"),
    (r"\bв данный момент\b", "сейчас"),
    (r"\bнеобходимо\b", "нужно"),
    (r"\bследует отметить\b", ""),
    (r"\bя думаю, что\b", "думаю,"),
    (r"\bмне кажется, что\b", "кажется,"),
    (r"\bтем не менее,\s*", "но "),
    (r"\bоднако,\s*", "но "),
]


_ENDPOINT_CACHE: dict = {"ts": 0.0, "value": None}
_ENDPOINT_TTL_S = 30.0


def _endpoint_cache_ttl() -> float:
    return _env_float("VM_LLM_ENDPOINT_TTL_S", _ENDPOINT_TTL_S)


def reset_endpoint_cache() -> None:
    _ENDPOINT_CACHE["ts"] = 0.0
    _ENDPOINT_CACHE["value"] = None


def _process_memory_mb() -> float | None:
    """Best-effort RSS for inference telemetry (optional psutil)."""
    try:
        import psutil  # type: ignore

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return None


def _resolve_endpoint() -> dict:
    """Active LLM endpoint via the central resolver (env → discovery → cloud).

    Cached for a short TTL: endpoint discovery (Ollama probe) previously ran on
    every _llm_chat (3+ times per call), re-discovering the local server each
    time and adding latency + log spam. The cache makes it run at most once per
    TTL window per process.
    """
    now = time.monotonic()
    cached = _ENDPOINT_CACHE.get("value")
    if cached is not None and (now - float(_ENDPOINT_CACHE.get("ts") or 0.0)) < _endpoint_cache_ttl():
        return cached

    # Production AI Router: apply selected Local / My API / Cloud without paywall.
    try:
        from core.ai_router import get_ai_router

        decision = get_ai_router().apply_route()
        if decision.available and decision.base_url:
            ep = {
                "available": True,
                "base_url": decision.base_url.rstrip("/"),
                "api_key": decision.api_key or _llm_api_key(),
                "models": [decision.model] if decision.model else [],
                "provider": decision.provider,
                "source": decision.source,
                "free": decision.free,
            }
            _ENDPOINT_CACHE["value"] = ep
            _ENDPOINT_CACHE["ts"] = now
            return ep
        if str(os.getenv("VM_AI_SOURCE_MODE") or "").lower() == "local" and not decision.available:
            # Explicit local-with-no-LLM: do not silently charge cloud API.
            ep = {
                "available": False,
                "base_url": "",
                "api_key": None,
                "models": [],
                "provider": "none",
                "source": "local",
                "free": True,
            }
            _ENDPOINT_CACHE["value"] = ep
            _ENDPOINT_CACHE["ts"] = now
            return ep
    except Exception:
        pass

    try:
        from engines.llm_adaptation_mode import resolve_llm_endpoint

        ep = resolve_llm_endpoint()
        _ENDPOINT_CACHE["value"] = ep
        _ENDPOINT_CACHE["ts"] = now
        return ep
    except Exception:
        base = os.getenv("VM_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        key = _llm_api_key()
        if base:
            return {"available": True, "base_url": base.rstrip("/"), "api_key": key, "models": []}
        if key:
            return {"available": True, "base_url": "https://api.openai.com/v1", "api_key": key, "models": []}
        return {"available": False, "base_url": "", "api_key": None, "models": []}


def _llm_base_url() -> str:
    """OpenAI-compatible endpoint. Supports self-hosted (Ollama/LM Studio/vLLM)."""
    ep = _resolve_endpoint()
    base = (ep.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    return f"{base}/chat/completions"


def _llm_api_key() -> str | None:
    return (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("VM_LLM_API_KEY")
        or os.getenv("VM_OPENAI_API_KEY")
    )


def _llm_model() -> str:
    """Resolve the chat model for the active endpoint (auto-selects local)."""
    ep = _resolve_endpoint()
    try:
        from engines.llm_adaptation_mode import resolve_llm_model

        return resolve_llm_model(ep.get("models"), provider=ep.get("provider", ""))
    except Exception:
        return os.getenv("VM_TRANSLATE_MODEL", "gpt-4o-mini")


def llm_rephrase_available() -> bool:
    """True when an LLM endpoint is usable.

    Resolved automatically: a cloud API key, an explicit self-hosted base URL, OR
    an auto-discovered local server (Ollama / LM Studio / OpenAI-compatible). No
    manual configuration required.
    """
    return bool(_resolve_endpoint().get("available"))


def _raw_chat_send(
    prompt: str,
    *,
    model: str,
    system: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float | None = None,
    endpoint_base: str | None = None,
) -> tuple[str | None, str, Exception | None]:
    """Pure OpenAI-compatible HTTP send. No guards / budget / semaphore.

    Returns (text, finish_reason, error). Used by both ``_llm_chat_once`` (which
    adds semaphore/budget/inflight around it) and the LLM Dispatcher adapters, so
    request bytes and parsing stay identical across every path.
    """
    try:
        import json
        import urllib.request

        headers = {"Content-Type": "application/json"}
        api_key = _llm_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")
        if endpoint_base:
            url = endpoint_base.rstrip("/") + "/chat/completions"
        else:
            url = _llm_base_url()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        _timeout = timeout if (timeout and timeout > 0) else _llm_call_timeout()
        with urllib.request.urlopen(req, timeout=_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        choice = (data.get("choices") or [{}])[0]
        out = ((choice.get("message") or {}).get("content") or "").strip()
        finish = str(choice.get("finish_reason") or "")
        if finish == "length":
            return None, finish, ValueError("token_limit")
        return (out or None), (finish or "stop"), None
    except Exception as exc:
        return None, "error", exc


def _dispatcher_route_enabled() -> bool:
    """Route the send through the LLM Dispatcher (TZ #3).

    Only engages when there is a real model choice to make (multiple models, a
    failover chain, or a hot-swapped active model). In the single-model case the
    direct send runs unchanged — no discovery, no overhead, identical behavior.
    """
    try:
        from core.llm_dispatcher import get_dispatcher

        return get_dispatcher().should_route()
    except Exception:
        return False


def _llm_chat_once(
    prompt: str,
    *,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float | None = None,
    count_budget: bool = True,
    system: str | None = None,
    model: str | None = None,
    segment: int | None = None,
    attempt: int = 1,
) -> tuple[str | None, Exception | None, dict[str, Any]]:
    """Single LLM call with budget/semaphore/inflight guards.

    The actual model selection + network send is delegated to the LLM Dispatcher
    (TZ #3) unless an explicit ``model`` was requested or the dispatcher is off,
    in which case the send goes straight to ``_raw_chat_send`` with the resolved
    model. Guards, logging and budget accounting are unchanged.
    """
    meta: dict[str, Any] = {"model": "", "provider": "", "attempt": attempt}
    resolved_model = str(model or _llm_model())
    meta["model"] = resolved_model
    ep = _resolve_endpoint()
    meta["provider"] = str(ep.get("provider") or "")
    api_url = str(ep.get("url") or ep.get("base_url") or "")
    if not api_url:
        try:
            api_url = _llm_base_url()
        except Exception:
            api_url = ""

    _t0 = time.monotonic()
    _timeout = timeout if (timeout and timeout > 0) else _llm_call_timeout()
    _sem = _get_llm_semaphore()
    _acquire_wait = float(_timeout) + _env_float("VM_LLM_SEM_ACQUIRE_HEADROOM_S", 30.0)
    _acquired = _sem.acquire(timeout=_acquire_wait)
    if not _acquired:
        if count_budget:
            _llm_record(time.monotonic() - _t0, ok=False)
            record_llm_skip("llm_semaphore_timeout")
        return None, TimeoutError("llm_semaphore_timeout"), meta

    logger.info("[LLM] Inference started (model=%s attempt=%s)", resolved_model, attempt)
    _set_llm_inflight(
        segment=segment if segment is not None else _llm_ctx.get("segment"),
        stage=_llm_ctx.get("stage"),
        model=resolved_model,
        provider=meta["provider"],
        api_url=api_url,
        chars_sent=len(str(prompt or "")),
        timeout_sec=_timeout,
        started_at=time.time(),
        timed_out=False,
        attempt=attempt,
    )
    text: str | None = None
    finish = "stop"
    err: Exception | None = None
    try:
        # Route through the Dispatcher (model choice + failover) unless caller
        # forced a specific model or the dispatcher is disabled.
        if model is None and _dispatcher_route_enabled():
            try:
                from core.llm_dispatcher import get_dispatcher

                text, err, dmeta = get_dispatcher().execute_chat(
                    prompt,
                    task_type="adapt",
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=_timeout,
                    segment=segment,
                    stage=str(_llm_ctx.get("stage") or ""),
                    allow_failover=True,
                )
                if dmeta.get("model"):
                    resolved_model = str(dmeta["model"])
                    meta["model"] = resolved_model
                    meta["provider"] = str(dmeta.get("provider") or meta["provider"])
                finish = "stop" if text else "error"
                # Registry saw no models but an endpoint exists → direct send.
                if text is None and int(dmeta.get("attempts", 0)) == 0:
                    text, finish, err = _raw_chat_send(
                        prompt, model=resolved_model, system=system,
                        max_tokens=max_tokens, temperature=temperature, timeout=_timeout,
                    )
            except Exception as exc:
                # Dispatcher failure must never break the pipeline — fall back to
                # the direct send with the resolved model.
                logger.warning("[DISPATCH] fell back to direct send: %s", exc)
                text, finish, err = _raw_chat_send(
                    prompt, model=resolved_model, system=system,
                    max_tokens=max_tokens, temperature=temperature, timeout=_timeout,
                )
        else:
            text, finish, err = _raw_chat_send(
                prompt, model=resolved_model, system=system,
                max_tokens=max_tokens, temperature=temperature, timeout=_timeout,
            )
    finally:
        _sem.release()
        _set_llm_inflight()

    _elapsed_ms = (time.monotonic() - _t0) * 1000.0
    if err is not None:
        if count_budget:
            _llm_record(time.monotonic() - _t0, ok=False)
            _record_llm_call(
                prompt, "", finish_reason="error", ms=_elapsed_ms, ok=False,
                provider=meta.get("provider") or "", model=resolved_model,
            )
        return None, err, meta
    if count_budget:
        _llm_record(time.monotonic() - _t0, ok=bool(text))
        _record_llm_call(
            prompt, text or "", finish_reason=finish or "stop", ms=_elapsed_ms, ok=bool(text),
            provider=meta["provider"], model=resolved_model,
        )
    return text or None, None, meta


def _llm_chat(
    prompt: str,
    *,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float | None = None,
    count_budget: bool = True,
    system: str | None = None,
) -> str | None:
    """Single-shot OpenAI-compatible chat call. Returns text or None.

    Results are cached (TZ §6): an unchanged prompt + model + algorithm version
    reuses the saved rewrite instead of calling the LLM again.

    ``timeout`` overrides the per-call socket timeout (used by the patient
    health check, where a cold model load may take a while). ``count_budget``
    False makes the call ignore — and not affect — the dub-run circuit breaker
    (also for health checks, which must never trip the breaker).
    """
    if not llm_rephrase_available():
        # Never a silent skip (audit §2): record why the LLM was not called so
        # the segment shows LLM_NOT_CALLED: no_endpoint instead of vanishing.
        if count_budget:
            record_llm_skip("no_endpoint")
        return None

    model = _llm_model()
    cache_key = None
    try:
        from engines.llm_adaptation_mode import ADAPTATION_ALGO_VERSION
        from engines.llm_cache import get as _cache_get
        from engines.llm_cache import make_key

        # Intelligent cache key (Task 4): text/lang/strategy are already encoded
        # in the prompt; add model + quality mode so different modes/models never
        # collide and a validated rewrite is reused across identical requests.
        _mode = adaptation_speed_mode()
        cache_prompt = f"[mode:{_mode}]\n{system}\n\n{prompt}" if system else f"[mode:{_mode}]\n{prompt}"
        cache_key = make_key(ADAPTATION_ALGO_VERSION, model, max_tokens, cache_prompt)
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.debug("[Adapt] LLM cache hit")
            return cached or None
    except Exception:
        cache_key = None

    # Per-segment guards only (ТЗ P0). There is NO global time breaker that can
    # skip a segment: every segment is guaranteed its first LLM attempt. A
    # cache hit above is always served (it is free). Health checks
    # (count_budget=False) bypass all guards.
    if count_budget:
        with _LLM_CALLS_LOCK:
            seg = _llm_ctx.get("segment")
            task_id = str(_llm_budget.get("task") or "")
        try:
            from engines.ai_core import llm_gateway

            allowed, reason = llm_gateway.can_call_llm(task_id, seg)
            if not allowed:
                record_llm_skip(reason or "blocked")
                return None
        except Exception:
            if circuit_open():
                record_llm_skip("llm_circuit_open")
                return None
            if _segment_time_budget_open(seg):
                record_llm_skip("segment_time_budget")
                return None
            if _segment_breaker_open(seg):
                record_llm_skip("segment_breaker_open")
                return None
    else:
        seg = None
        task_id = ""

    if not count_budget:
        _timeout = timeout if (timeout and timeout > 0) else _llm_call_timeout()
        out, _, _meta = _llm_chat_once(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=_timeout,
            count_budget=False,
            system=system,
            model=model,
        )
        return out

    from engines.llm_retry_manager import run_with_retry

    def _once(p, **kw):
        return _llm_chat_once(p, **kw)

    outcome = run_with_retry(
        _once,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        count_budget=True,
        task_id=task_id,
        segment=seg,
    )
    out = outcome.text
    if out and cache_key:
        try:
            from engines.llm_cache import put as _cache_put

            _cache_put(cache_key, out)
        except Exception:
            pass
    if not out and count_budget and outcome.failure_phase:
        with _LLM_CALLS_LOCK:
            row = _seg_status(seg)
            row["skip_reason"] = outcome.failure_phase or outcome.failure or "error"
    return out or None


def _lang_label(tgt_lang: str) -> str:
    try:
        from engines.translation_naturalizer import _lang_name
        from engines.mt.lang_codes import normalize_lang

        return _lang_name(normalize_lang(tgt_lang))
    except Exception:
        try:
            from data.languages import LANG_CODE_TO_NAME

            return LANG_CODE_TO_NAME.get(tgt_lang, tgt_lang)
        except Exception:
            return tgt_lang


def _llm_shorten(
    text: str,
    source_hint: str,
    target_ratio: float,
    tgt_lang: str = "ru",
) -> str | None:
    if not text.strip():
        return None
    pct = int(max(55, min(92, target_ratio * 100)))
    lang_label = _lang_label(tgt_lang)
    prompt = (
        f"Rewrite this dubbing line shorter in {lang_label} for natural spoken delivery. "
        f"Target about {pct}% of the current length (by speaking time, not by deleting the ending). "
        "Preserve the FULL meaning: every actor/name, action, emotion, and outcome from the original. "
        "Use shorter natural phrasing and idioms a native speaker would say. "
        "Never truncate mid-sentence. Never end with ellipsis. "
        "Output only the rewritten line, no quotes or explanation.\n"
    )
    if source_hint.strip():
        prompt += f"Original speech (reference): {source_hint.strip()}\n"
    prompt += f"Translated line: {text.strip()}"
    return _llm_chat(prompt, max_tokens=512)


def _llm_expand(
    text: str,
    source_hint: str,
    target_ratio: float,
    tgt_lang: str = "ru",
) -> str | None:
    """Rephrase a too-short line into a natural, slightly LONGER one (TZ §3).

    Lengthening must come only from natural rephrasing — never filler words,
    repetition, or padding.
    """
    if not text.strip():
        return None
    pct = int(max(105, min(180, target_ratio * 100)))
    lang_label = _lang_label(tgt_lang)
    prompt = (
        f"Rephrase this dubbing line in {lang_label} so it takes a bit LONGER to say "
        f"(about {pct}% of the current speaking time), to better match the original timing. "
        "Keep EXACTLY the same meaning, names, and facts. "
        "Make it longer only with natural, fuller phrasing a native speaker would use — "
        "never add filler words, never repeat words, never pad with meaningless phrases. "
        "Keep it one grammatically complete sentence (or the same number of sentences). "
        "Output only the rephrased line, no quotes or explanation.\n"
    )
    if source_hint.strip():
        prompt += f"Original speech (reference): {source_hint.strip()}\n"
    prompt += f"Line to lengthen: {text.strip()}"
    return _llm_chat(prompt, max_tokens=600)


def _stage_minimal(text: str) -> str:
    out = _FILLERS_UNIVERSAL.sub("", text)
    for pattern, repl in _SHORTEN_PATTERNS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return " ".join(out.split())


def _stage_moderate(text: str) -> str:
    """Remove softeners only — do not drop clauses (semantic blocks)."""
    out = _SOFTENERS_UNIVERSAL.sub("", text)
    return " ".join(out.split())


def _stage_semantic_rephrase(
    text: str,
    target_ratio: float,
    source_hint: str = "",
    tgt_lang: str = "ru",
) -> str:
    """
    Full-sentence rephrase via LLM — never tail-clip words (TZ §1–2).
    Returns original if rephrase unavailable or rejected.
    """
    llm = _llm_shorten(text, source_hint, target_ratio, tgt_lang=tgt_lang)
    if not llm:
        return text
    candidate = " ".join(llm.split())
    min_words = max(3, int(len(text.split()) * max(0.55, target_ratio * 0.85)))
    if word_count(candidate) < min_words:
        return text
    from engines.semantic_meaning import verify_meaning_preserved

    ok, reason, _ = verify_meaning_preserved(
        source_hint,
        text,
        candidate,
        target_lang=tgt_lang,
    )
    if not ok:
        logger.info("[Adapt] semantic rephrase rejected: %s", reason)
        return text
    return candidate


def word_count(text: str) -> int:
    return len(str(text or "").split())


def adapt_translation_shorter(
    text: str,
    *,
    target_ratio: float = 0.85,
    source_hint: str = "",
    allow_llm: bool = True,
    stage: str = "auto",
    tgt_lang: str = "ru",
) -> str:
    """
    Поэтапное сокращение: minimal → moderate → strong → LLM.
    stage: minimal | moderate | strong | auto
    """
    original = " ".join(str(text or "").split())
    if not original:
        return original

    ratio = max(0.55, min(1.0, float(target_ratio)))
    if ratio >= 0.98:
        return original

    out = original
    from engines.semantic_meaning import apply_compact_phrases

    out = apply_compact_phrases(out, target_lang=tgt_lang)
    if stage in ("minimal", "auto"):
        out = _stage_minimal(out)
    if stage in ("moderate", "auto") and word_count(out) > max(
        3, int(len(original.split()) * ratio)
    ):
        out = _stage_moderate(out)

    needs_shorter = word_count(out) > max(3, int(len(original.split()) * ratio))
    if stage in ("strong", "auto") and needs_shorter:
        rephrased = _stage_semantic_rephrase(
            out, ratio, source_hint=source_hint, tgt_lang=tgt_lang
        )
        if rephrased != out:
            out = rephrased
        elif allow_llm:
            llm = _llm_shorten(out, source_hint, ratio, tgt_lang=tgt_lang)
            if llm:
                from engines.semantic_meaning import verify_meaning_preserved

                candidate = " ".join(llm.split())
                ok, reason, _ = verify_meaning_preserved(
                    source_hint, out, candidate, target_lang=tgt_lang
                )
                if ok:
                    out = candidate
                else:
                    logger.info("[Adapt] LLM shorten rejected: %s", reason)

    out = " ".join(out.split())
    if not out:
        return original
    from engines.semantic_meaning import verify_meaning_preserved, is_truncated_adaptation

    if is_truncated_adaptation(original, out):
        return original
    ok, reason, _ = verify_meaning_preserved(
        source_hint, original, out, target_lang=tgt_lang
    )
    if not ok:
        logger.info("[Adapt] final meaning check failed: %s — keeping original", reason)
        return original
    if word_count(out) < max(2, int(word_count(original) * 0.45)):
        return original
    return out


def adapt_for_duration(
    text: str,
    current_ms: int,
    target_ms: int,
    source_hint: str = "",
    *,
    stage: str = "auto",
    tgt_lang: str = "ru",
) -> str:
    """Подбирает сокращение под целевую длительность TTS (time budget, not char count)."""
    if not text or current_ms <= 0 or target_ms <= 0:
        return text
    if current_ms <= target_ms * 1.03:
        return text

    from engines.semantic_optimizer import optimize_for_time_budget

    slot_ms = int(target_ms) + 40
    result = optimize_for_time_budget(
        text,
        source_hint=source_hint,
        slot_ms=slot_ms,
        tgt_lang=tgt_lang,
        allow_minimal_removal=False,
        allow_llm=True,
    )
    adapted = result.text
    if adapted != text:
        logger.info(
            "[Adapt] time_budget slot=%dms delta=%d stages=%d reason=%s",
            result.budget.segment_duration_ms,
            result.budget.delta_ms,
            len(result.stages),
            result.stopped_reason,
        )
    return adapted


def llm_adapt_segment(
    prompt: str,
    *,
    segment_index: int,
    source_text: str = "",
    translated_text: str = "",
    target_lang: str = "",
    context_before: str = "",
    context_after: str = "",
    system: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.2,
    task_id: str = "",
    stage: str = "ai_adaptation",
    allow_backup: bool = True,
) -> str | None:
    """Quality-first segment adaptation via the LLM Orchestrator.

    When ``VM_LLM_ORCHESTRATOR`` is enabled (default), routes the segment to
    the best available model for its difficulty. On timeout / empty response,
    a backup model may retry (never on every segment). Falls back to
    :func:`_llm_chat` when the orchestrator is disabled.

    Quality is never sacrificed: circuit breakers and meaning checks upstream
    remain authoritative; this function only improves *which* model serves the
    segment.
    """
    import os

    use_orch = str(os.getenv("VM_LLM_ORCHESTRATOR", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if not use_orch:
        return _llm_chat(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )

    try:
        from engines.llm_orchestrator import LLMTask, get_llm_orchestrator

        orch = get_llm_orchestrator()
        result = orch.run_sync(
            LLMTask(
                segment_index=segment_index,
                prompt=prompt,
                system=system or "",
                source_text=source_text,
                translated_text=translated_text,
                target_lang=target_lang,
                context_before=context_before,
                context_after=context_after,
                max_tokens=max_tokens,
                temperature=temperature,
                stage=stage,
                task_id=task_id,
                allow_backup=allow_backup,
            )
        )
        if result.ok and result.text:
            return result.text
        if result.skip_reason:
            record_llm_skip(result.skip_reason)
        return None
    except Exception:
        logger.debug("llm_adapt_segment orchestrator failed, falling back", exc_info=True)
        return _llm_chat(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )


def llm_adapt_segment(
    prompt: str,
    *,
    segment_index: int,
    source_text: str = "",
    translated_text: str = "",
    target_lang: str = "",
    context_before: str = "",
    context_after: str = "",
    system: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.2,
    task_id: str = "",
    stage: str = "ai_adaptation",
    allow_backup: bool = True,
) -> str | None:
    """Quality-first segment adaptation via the LLM Orchestrator.

    When ``VM_LLM_ORCHESTRATOR`` is enabled (default), routes the segment to
    the best available model for its difficulty. On timeout / empty response,
    a backup model may retry (never on every segment). Falls back to
    :func:`_llm_chat` when the orchestrator is disabled.

    Quality is never sacrificed: circuit breakers and meaning checks upstream
    remain authoritative; this function only improves *which* model serves the
    segment.
    """
    import os

    use_orch = str(os.getenv("VM_LLM_ORCHESTRATOR", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if not use_orch:
        return _llm_chat(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )

    try:
        from engines.llm_orchestrator import LLMTask, get_llm_orchestrator

        orch = get_llm_orchestrator()
        result = orch.run_sync(
            LLMTask(
                segment_index=segment_index,
                prompt=prompt,
                system=system or "",
                source_text=source_text,
                translated_text=translated_text,
                target_lang=target_lang,
                context_before=context_before,
                context_after=context_after,
                max_tokens=max_tokens,
                temperature=temperature,
                stage=stage,
                task_id=task_id,
                allow_backup=allow_backup,
            )
        )
        if result.ok and result.text:
            return result.text
        if result.skip_reason:
            record_llm_skip(result.skip_reason)
        return None
    except Exception:
        logger.debug("llm_adapt_segment orchestrator failed, falling back", exc_info=True)
        return _llm_chat(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )

