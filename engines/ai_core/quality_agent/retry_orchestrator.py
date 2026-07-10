"""Retry orchestrator — max 3 returns per segment, re-run ONLY responsible agent."""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger("tubedub.ai_core.quality_agent.retry")

MAX_RETRIES = 3


def _llm_available() -> bool:
    try:
        from engines.ai_core import llm_gateway

        return bool(llm_gateway.is_available())
    except Exception:
        return False


def rerun_agent_for_segment(
    agent_name: str,
    segment_index: int,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
) -> dict:
    """
    Re-run a single agent on one segment index.

    Uses agent internals with state containing only the target segment context.
    """
    from engines.ai_core.ai_network.bridge import emit_agent_finished, emit_agent_started, emit_recovery_action

    segments = state.get("segments") or []
    if segment_index < 0 or segment_index >= len(segments):
        return {}

    seg = segments[segment_index]
    idx = int(seg.get("index", segment_index))
    agent_key = agent_name.replace("Agent", "").lower()
    emit_recovery_action(
        task_id,
        from_agent="quality",
        to_agent=agent_key,
        segment_index=idx,
        reason="segment_retry",
    )
    emit_agent_started(task_id, agent_key, segment_index=idx)

    if agent_name == "TranslationAgent":
        result = _rerun_translation(seg, manifest, state, task_id, idx)
    elif agent_name == "SemanticAgent":
        result = _rerun_semantic(seg, manifest, state, task_id, idx, segment_index)
    elif agent_name == "TimingAgent":
        result = _rerun_timing(seg, manifest, state, task_id, idx, segment_index)
    elif agent_name == "GrammarAgent":
        result = _rerun_grammar(seg, manifest, state, task_id, idx, segment_index)
    else:
        logger.warning("Unknown agent for retry: %s", agent_name)
        result = seg

    emit_agent_finished(task_id, agent_key, status="success", segment_index=idx)
    from engines.ai_core.reviewer_gate import review_agent_output

    review_agent_output(
        task_id,
        agent_key,
        segments=[result] if result else [],
        tgt_lang=str(manifest.get("target_lang") or ""),
    )
    return result


def _rerun_translation(
    seg: dict,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    idx: int,
) -> dict:
    from engines.ai_core.translation_agent.agent import TranslationAgent
    from engines.ai_core.translation_agent.translator_interface import TranslatorRegistry
    from engines.ai_core.translation_agent.retry_policy import translate_with_fallback
    from engines.mt.lang_codes import normalize_lang

    source = normalize_lang(manifest.get("source_lang") or "en")
    target = normalize_lang(manifest.get("target_lang") or "ru")
    text = str(seg.get("text") or "").strip()
    if not text:
        return seg

    agent = TranslationAgent()
    registry = TranslatorRegistry()
    result = translate_with_fallback(
        text,
        source,
        target,
        registry,
        threshold=agent.confidence_threshold,
    )
    seg = copy.deepcopy(seg)
    seg["translated_text"] = result.translated
    seg["translator_used"] = result.translator_name
    logger.info("Quality retry TranslationAgent segment_%s task=%s", idx, task_id)
    return seg


def _rerun_semantic(
    seg: dict,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    idx: int,
    segment_index: int,
) -> dict:
    from engines.ai_core.entity_dictionary import EntityDictionary
    from engines.ai_core.semantic_agent.agent import SemanticAgent
    from engines.ai_core.semantic_engine.context_bundle import build_dialogue_context
    from engines.mt.lang_codes import normalize_lang

    agent = SemanticAgent()
    source = normalize_lang(manifest.get("source_lang") or "en")
    target = normalize_lang(manifest.get("target_lang") or "ru")
    seg_copy = copy.deepcopy(seg)
    stats: dict[str, Any] = {"variants_generated": 0, "warnings": []}
    decision_log: list[str] = []

    segments = state.get("segments") or []
    entity_dict = EntityDictionary.from_segments(segments, target_lang=target, manifest=manifest)
    dialogue = build_dialogue_context(segments, segment_index, manifest)

    agent._process_segment(
        seg_copy,
        src_lang=source,
        tgt_lang=target,
        dialogue=dialogue,
        entity_dict=entity_dict,
        stats=stats,
        decision_log=decision_log,
        force_llm=True,
    )
    segments[segment_index] = seg_copy
    state["segments"] = segments
    logger.info("Quality retry SemanticAgent segment_%s task=%s", idx, task_id)
    return seg_copy


def _rerun_timing(
    seg: dict,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    idx: int,
    segment_index: int,
) -> dict:
    from engines.ai_core.timing_agent.agent import TimingAgent
    from engines.mt.lang_codes import normalize_lang

    agent = TimingAgent(use_llm=_llm_available())
    target = normalize_lang(manifest.get("target_lang") or "ru")
    seg_copy = copy.deepcopy(seg)
    stats: dict[str, Any] = {"warnings": []}
    decision_log: list[str] = []

    agent._process_segment(
        seg_copy,
        tgt_lang=target,
        stats=stats,
        decision_log=decision_log,
    )
    segments = state.get("segments") or []
    segments[segment_index] = seg_copy
    state["segments"] = segments
    logger.info("Quality retry TimingAgent segment_%s task=%s", idx, task_id)
    return seg_copy


def _rerun_grammar(
    seg: dict,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    idx: int,
    segment_index: int,
) -> dict:
    from engines.ai_core.grammar_agent.agent import GrammarAgent
    from engines.mt.lang_codes import normalize_lang

    agent = GrammarAgent(use_llm=_llm_available())
    target = normalize_lang(manifest.get("target_lang") or "ru")
    seg_copy = copy.deepcopy(seg)
    stats: dict[str, Any] = {"variants_generated": 0, "warnings": []}
    decision_log: list[str] = []

    prev_context = None
    segments = state.get("segments") or []
    if segment_index > 0:
        prev = segments[segment_index - 1]
        prev_context = str(prev.get("grammar_text") or prev.get("timing_text") or "")

    agent._process_segment(
        seg_copy,
        tgt_lang=target,
        prev_context=prev_context,
        stats=stats,
        decision_log=decision_log,
    )
    segments[segment_index] = seg_copy
    state["segments"] = segments
    logger.info("Quality retry GrammarAgent segment_%s task=%s", idx, task_id)
    return seg_copy
