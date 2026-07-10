"""TubeDub Global Skill — unified rules for all AI agents (TZ #1 §1)."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.global_skill")

_RULES_PATH = Path(__file__).with_name("rules.json")


@lru_cache(maxsize=1)
def load_skill() -> dict[str, Any]:
    try:
        return json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Global Skill load failed: %s", exc)
        return {
            "version": "1.0",
            "rules": [],
            "llm_system_preamble": "Preserve meaning and target language.",
        }


def skill_version() -> str:
    return str(load_skill().get("version") or "1.0")


def rule_ids() -> list[str]:
    return [str(r.get("id") or "") for r in load_skill().get("rules") or []]


def principles() -> list[str]:
    return list(load_skill().get("principles") or [])


def augment_system_prompt(system: str | None) -> str:
    """Inject Global Skill preamble into LLM system prompts."""
    preamble = str(load_skill().get("llm_system_preamble") or "").strip()
    if not preamble:
        return str(system or "").strip()
    base = str(system or "").strip()
    if preamble in base:
        return base
    return f"{preamble}\n\n{base}".strip() if base else preamble


def to_dict() -> dict[str, Any]:
    data = load_skill()
    return {
        "version": data.get("version"),
        "title": data.get("title"),
        "principles": data.get("principles") or [],
        "rule_count": len(data.get("rules") or []),
        "rules": data.get("rules") or [],
    }


def check_agent_result(
    agent_name: str,
    *,
    status: str = "",
    segments: list[dict[str, Any]] | None = None,
    tgt_lang: str = "",
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Lightweight Global Skill compliance check for Reviewer (TZ #1 §2)."""
    violations: list[dict[str, str]] = []
    warnings: list[str] = []

    if status == "error" and not (errors or []):
        violations.append(
            {"rule": "no_silent_failure", "message": f"{agent_name}: error without diagnostics"}
        )

    lang = str(tgt_lang or "").strip().lower()
    for seg in segments or []:
        idx = seg.get("index", seg.get("segment_index"))
        text = _segment_output_text(agent_name, seg)
        if not text:
            continue
        if lang and _looks_like_wrong_language(text, lang):
            violations.append(
                {
                    "rule": "target_language_only",
                    "message": f"segment {idx}: output not in target language ({lang})",
                }
            )
        if _has_truncation_artifact(text):
            violations.append(
                {
                    "rule": "no_text_truncation",
                    "message": f"segment {idx}: truncated or incomplete text",
                }
            )

    approved = not violations
    return {
        "approved": approved,
        "agent": agent_name,
        "violations": violations,
        "warnings": warnings,
        "skill_version": skill_version(),
    }


def _segment_output_text(agent_name: str, seg: dict[str, Any]) -> str:
    field_map = {
        "translation": "translated_text",
        "semantic": "semantic_text",
        "timing": "timing_text",
        "grammar": "grammar_text",
        "quality": "grammar_text",
        "reviewer": "final_text",
    }
    key = field_map.get(agent_name, "text")
    return str(seg.get(key) or seg.get("text") or "").strip()


def _looks_like_wrong_language(text: str, tgt_lang: str) -> bool:
    if tgt_lang in ("uk", "ua", "ukrainian"):
        cyr = len(re.findall(r"[\u0400-\u04FF]", text))
        lat = len(re.findall(r"[A-Za-z]", text))
        words = len(text.split())
        if words >= 6 and lat > cyr * 1.5:
            return True
    return False


def _has_truncation_artifact(text: str) -> bool:
    t = text.strip()
    if t.endswith("...") or t.endswith("…"):
        return True
    if t.endswith(",") or t.endswith(";"):
        return True
    if len(t) > 20 and t[-1] not in ".!?…»\"')":
        return False
    return False
