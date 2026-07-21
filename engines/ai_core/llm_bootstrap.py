"""Prepare LLM endpoint + budget before AI Core agent runs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.llm_bootstrap")


def prepare_llm_for_pipeline(
    task_id: str,
    info: dict[str, Any] | None = None,
    *,
    app_dir: Path | None = None,
    phase: str = "AI_CORE",
) -> dict[str, Any]:
    """Discover endpoint, ensure callable model, warm model, begin per-run LLM budget.

    Call before Semantic/Grammar/Timing agents and before timing-aware adaptation
    so ``llm_gateway.is_available()`` reflects reality and agents can invoke LLM.
    """
    info = info or {}
    status: dict[str, Any] = {"task_id": task_id, "available": False, "phase": phase}

    try:
        from engines.translation_adapt import reset_circuit_breaker, reset_endpoint_cache
        from engines.llm_callable import reset_run_state

        reset_circuit_breaker()
        reset_endpoint_cache()
        reset_run_state()
    except Exception:
        pass

    try:
        from engines.ai_core import llm_gateway

        llm_gateway.reset_circuit_for_phase(phase)
    except Exception:
        pass

    try:
        from engines.llm_callable import ensure_llm_callable, apply_to_task_info

        callable_status = ensure_llm_callable(
            app_dir=app_dir,
            task_id=task_id,
            max_attempts=3,
        )
        status["callable"] = bool(callable_status.get("callable"))
        status["discovered"] = bool(callable_status.get("llm_available"))
        status["discovered_provider"] = callable_status.get("provider")
        status["discovered_base"] = callable_status.get("base_url")
        status["model"] = callable_status.get("model")
        status["installed_models"] = callable_status.get("installed_models") or []
        status["remediation"] = callable_status.get("remediation")
        status["callable_attempts"] = callable_status.get("attempts")
        status["fatal_reason"] = callable_status.get("fatal_reason")
        apply_to_task_info(info, callable_status)
    except Exception as exc:
        logger.debug("LLM callable ensure skipped: %s", exc)
        try:
            from engines.llm_adaptation_mode import discover_local_llm

            discovered = discover_local_llm(force=True)
            status["discovered"] = bool(discovered)
            if discovered:
                status["discovered_provider"] = discovered.get("provider")
                status["discovered_base"] = discovered.get("base_url")
        except Exception as disc_exc:
            logger.debug("LLM discovery skipped: %s", disc_exc)

    try:
        if app_dir is not None and status.get("callable"):
            from engines.ai_manager.installer import warmup_ai_for_dub

            status["warmed"] = bool(warmup_ai_for_dub(app_dir))
        else:
            status["warmed"] = False
    except Exception as exc:
        logger.debug("LLM warmup skipped: %s", exc)
        status["warmed"] = False

    mode = info.get("adaptation_speed_mode") or info.get("dub_speed_mode")
    per_seg = info.get("adaptation_segment_budget_s")
    proj = info.get("adaptation_project_budget_s")

    try:
        from engines.ai_core import llm_gateway

        llm_gateway.begin_run(
            task_id,
            mode=str(mode) if mode else None,
            per_segment_s=float(per_seg) if per_seg is not None else None,
            project_s=float(proj) if proj is not None else None,
        )
        status["available"] = bool(status.get("callable")) and bool(llm_gateway.is_available())
        status["endpoint"] = llm_gateway.active_model() or status.get("model") or ""
        status["budget"] = llm_gateway.status()
        if not status["available"]:
            try:
                from engines.translation_adapt import llm_rephrase_available, circuit_open

                status["endpoint_resolvable"] = bool(llm_rephrase_available())
                status["circuit_open"] = bool(circuit_open())
            except Exception:
                pass
    except Exception as exc:
        logger.warning("[LLM Bootstrap] begin_run failed: %s", exc)
        status["error"] = str(exc)

    logger.info(
        "[LLM Bootstrap] task=%s phase=%s callable=%s model=%s remediation=%s circuit_open=%s",
        task_id,
        phase,
        status.get("callable"),
        status.get("model") or status.get("endpoint") or "?",
        status.get("remediation"),
        status.get("circuit_open"),
    )
    return status
