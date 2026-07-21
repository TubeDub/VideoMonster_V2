"""LLM Refiner — selective refinement (never 100% of text to LLM)."""

from __future__ import annotations

import logging
from typing import Any

from engines.streamdub.base import ModuleCapabilities, StreamModule
from engines.streamdub.types import QualityGrade, StreamSegment

logger = logging.getLogger("tubedub.streamdub.llm_refiner")


class LLMRefiner(StreamModule):
    module_id = "llm_refiner"

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        self._app_dir = app_dir

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        try:
            from engines.translation_adapt import llm_rephrase_available

            ok = llm_rephrase_available()
            return ok, "available" if ok else "no_endpoint", None
        except Exception as exc:
            return False, str(exc), None

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            backends=["ollama", "openai", "anthropic", "deepseek", "gemma", "mistral"],
            features=["spot_fix_medium", "full_rewrite_bad", "skip_good"],
        )

    def _spot_fix(self, seg: StreamSegment, tgt: str) -> str | None:
        issues = ", ".join(seg.quality_issues[:3])
        prompt = (
            f"Fix ONLY the listed issues in this {tgt} dubbing line. "
            f"Issues: {issues}. Keep length similar. Output only the fixed line.\n"
            f"Original: {seg.text}\nLine: {seg.translated}"
        )
        try:
            from engines.translation_adapt import _llm_chat

            out = _llm_chat(prompt, max_tokens=256)
            return str(out).strip() if out else None
        except Exception as exc:
            logger.debug("spot_fix failed: %s", exc)
            return None

    def _full_rewrite(self, seg: StreamSegment, tgt: str) -> str | None:
        prompt = (
            f"Rewrite this dubbing line in natural {tgt}. "
            f"Preserve all names, numbers, and meaning. One sentence.\n"
            f"Original: {seg.text}\nDraft: {seg.translated}"
        )
        try:
            from engines.translation_adapt import _llm_chat

            out = _llm_chat(prompt, max_tokens=400)
            return str(out).strip() if out else None
        except Exception as exc:
            logger.debug("full_rewrite failed: %s", exc)
            return None

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        segments: list[StreamSegment] = list(payload.get("segments") or [])
        tgt = str(payload.get("target_lang") or "uk")
        entity_mgr = payload.get("entity_manager")
        force_all = bool(payload.get("force_llm_all"))

        refined = 0
        skipped = 0
        for seg in segments:
            grade = seg.quality or QualityGrade.GOOD
            if not force_all and grade == QualityGrade.GOOD:
                skipped += 1
                continue

            new_text = None
            if force_all or grade == QualityGrade.BAD:
                new_text = self._full_rewrite(seg, tgt)
                seg.route = "llm_full"
            elif grade == QualityGrade.MEDIUM:
                new_text = self._spot_fix(seg, tgt)
                seg.route = "llm_spot"

            if new_text:
                if entity_mgr is not None:
                    new_text = entity_mgr.apply(new_text, original=seg.text)
                seg.translated = new_text
                seg.llm_refined = True
                refined += 1
            else:
                skipped += 1

        total = max(1, len(segments))
        return {
            "segments": segments,
            "llm_refined": refined,
            "llm_skipped": skipped,
            "llm_pct": round(100.0 * refined / total, 1),
        }
