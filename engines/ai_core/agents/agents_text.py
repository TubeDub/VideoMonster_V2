"""AI Core 3.0 — Text agents.

The six agents that read/verify/rewrite the segment text, each doing exactly ONE
job and reusing the existing, already-hardened engines:

* :class:`TranslationAgent` — faithful literal translation (Raw Translation).
  Cheap path: pass through the upstream MT translation. LLM path: only when the
  Semantic/Entity agents send the segment back (re-translate for fidelity).
* :class:`SemanticAgent` — verify meaning preserved
  (:func:`engines.semantic_meaning.verify_meaning_preserved`); on loss routes
  back to Translation. Rule-based, never LLM.
* :class:`EntityAgent` — verify people/places/orgs/dates/numbers preserved
  (:func:`engines.semantic_meaning.compute_entity_preservation_score`); any loss
  routes back to Translation. Rule-based, never LLM.
* :class:`TimingAgent` — the reformulation-under-duration engine. Reuses
  :func:`engines.timing_aware_translation.adapt_segment_to_slot` (rule prep →
  LLM rewrite → anti-truncation), i.e. the existing Timing core. Skips work when
  the line already fits.
* :class:`GrammarAgent` — grammar/punctuation/naturalness only, never meaning.
  Cheap path: rule-based naturalizers. LLM path: gated polish with a meaning
  guard that reverts on loss.
* :class:`QualityAgent` — the gate. Checks meaning, grammar, timing, entities,
  sentence integrity, naturalness and slot fit; on failure returns work to ONLY
  the responsible agent (never restarts the whole chain).
"""

from __future__ import annotations

import logging

from engines.ai_core.agents.base import Agent, AgentResult, SegmentContext

logger = logging.getLogger("tubedub.ai_core.agents")


def _wc(text: str) -> int:
    return len(str(text or "").split())


# ─────────────────────────────────────────────────────────────────────────────
# 2. Translation Agent
# ─────────────────────────────────────────────────────────────────────────────
class TranslationAgent(Agent):
    """Faithful literal translation only — no shorten/lengthen/restructure."""

    name = "translation"

    def _run(self, ctx: SegmentContext) -> AgentResult:
        incoming = str(ctx.text or "").strip()
        raw = str(ctx.raw_translation or "").strip()
        # Normal flow: the upstream MT already produced the faithful translation;
        # the Translation Agent simply anchors it as the Raw Translation.
        if not ctx.diagnostics.get("force_retranslate"):
            chosen, reason, errors = self._anchor_raw_translation(
                ctx, incoming=incoming, raw=raw
            )
            ctx.text = chosen
            ctx.raw_translation = raw or incoming
            return AgentResult(
                agent=self.name,
                text=ctx.text,
                changed=(ctx.text != incoming),
                reason=reason,
                diagnostics={
                    "input_data": {
                        "incoming_text": incoming[:400],
                        "raw_translation": raw[:400],
                        "source_text": str(ctx.source_text or "")[:400],
                    },
                    "output_data": {"text": ctx.text[:400]},
                    "errors": errors,
                },
            )

        # Repair flow: Semantic/Entity found a fidelity loss and sent it back.
        ctx.diagnostics["force_retranslate"] = False
        if not ctx.allow_llm or not ctx.source_text.strip():
            return AgentResult(
                agent=self.name, text=ctx.text, changed=False,
                reason="retranslate_unavailable",
            )

        ck = self.cache.key(ctx.source_text, ctx.tgt_lang, ctx.model, "literal")
        cached = self.cache.get(ck)
        if cached and cached.get("text"):
            ctx.text = cached["text"]
            return AgentResult(
                agent=self.name, text=ctx.text, changed=True, cache_hit=True,
                reason="retranslate_cached",
                diagnostics={
                    "input_data": {
                        "incoming_text": incoming[:400],
                        "raw_translation": raw[:400],
                        "source_text": str(ctx.source_text or "")[:400],
                    },
                    "output_data": {"text": ctx.text[:400]},
                },
            )

        from engines.ai_core import llm_gateway

        lang = ctx.tgt_lang or "the target language"
        prompt = (
            f"Translate the following line into {lang}. Translate faithfully and "
            f"literally: keep every fact, name, number and nuance, do not shorten, "
            f"lengthen, restructure or add anything. Return ONLY the translation.\n\n"
            f"{ctx.source_text}"
        )
        out = llm_gateway.chat(prompt, max_tokens=256, temperature=0.0)
        text = str(out or "").strip().strip('"').strip()
        if not text:
            return AgentResult(
                agent=self.name, text=ctx.text, changed=False,
                reason="retranslate_empty",
                diagnostics={
                    "model": llm_gateway.active_model(),
                    "input_data": {
                        "incoming_text": incoming[:400],
                        "raw_translation": raw[:400],
                        "source_text": str(ctx.source_text or "")[:400],
                    },
                    "output_data": {"text": ctx.text[:400]},
                    "errors": ["empty_retranslate"],
                },
            )
        # Never emit source-language leak.
        try:
            from engines.pipeline_language_gate import is_critical_language_mismatch

            bad, _ = is_critical_language_mismatch(
                text, target_lang=ctx.tgt_lang, original=ctx.source_text
            )
            if bad:
                return AgentResult(
                    agent=self.name, text=ctx.text, changed=False,
                    reason="retranslate_lang_mismatch", used_llm=True,
                    diagnostics={
                        "model": llm_gateway.active_model(),
                        "input_data": {
                            "incoming_text": incoming[:400],
                            "raw_translation": raw[:400],
                            "source_text": str(ctx.source_text or "")[:400],
                        },
                        "output_data": {"text": ctx.text[:400]},
                        "errors": ["retranslate_lang_mismatch"],
                    },
                )
        except Exception:
            pass
        ctx.text = text
        ctx.raw_translation = text
        self.cache.put(ck, {"text": text})
        return AgentResult(
            agent=self.name, text=text, changed=True, used_llm=True,
            reason="retranslated",
            diagnostics={
                "model": llm_gateway.active_model(),
                "input_data": {
                    "incoming_text": incoming[:400],
                    "raw_translation": raw[:400],
                    "source_text": str(ctx.source_text or "")[:400],
                },
                "output_data": {"text": text[:400]},
            },
        )

    @staticmethod
    def _anchor_raw_translation(
        ctx: SegmentContext, *, incoming: str, raw: str
    ) -> tuple[str, str, list[str]]:
        """Prefer faithful raw MT, but never emit empty or source-language text."""
        chosen = raw or incoming
        reason = "passthrough_mt"
        errors: list[str] = []
        if raw:
            reason = "anchor_raw_mt" if raw != incoming else "passthrough_mt"
        else:
            errors.append("missing_raw_translation")

        if chosen:
            try:
                from engines.pipeline_language_gate import is_critical_language_mismatch

                bad, _ = is_critical_language_mismatch(
                    chosen, target_lang=ctx.tgt_lang, original=ctx.source_text
                )
                if bad and incoming and incoming != chosen:
                    errors.append("raw_translation_language_mismatch")
                    return incoming, "raw_mt_lang_mismatch_keep_current", errors
            except Exception:
                pass

        if not chosen and incoming:
            return incoming, "empty_raw_keep_current", errors + ["empty_raw_translation"]
        return chosen, reason, errors


# ─────────────────────────────────────────────────────────────────────────────
# 3. Semantic Agent
# ─────────────────────────────────────────────────────────────────────────────
class SemanticAgent(Agent):
    """Semantic pass: generate safe semantic variants, choose the best, and
    verify meaning preservation; on loss send the segment back to Translation.

    Responsibility boundary:
    * allowed: semantic polish, calque cleanup, duplicate-meaning cleanup,
      meaning/entity verification, choosing the best semantic-safe variant
    * forbidden: translation, timing-to-slot decisions, grammar-only polish
    """

    name = "semantic"

    def _run(self, ctx: SegmentContext) -> AgentResult:
        from engines.semantic_meaning import (
            compute_entity_preservation_score,
            compute_meaning_loss_score,
            verify_meaning_preserved,
        )
        from engines.semantic_translation import detect_semantic_issues

        original = str(ctx.text or "").strip()
        baseline = str(ctx.raw_translation or ctx.text or "").strip()

        chosen, variants, variant_reason = self._choose_semantic_variant(ctx, baseline=baseline)
        if chosen and chosen != original:
            logger.info(
                "[AIAgents] semantic idx=%d chose variant %s",
                ctx.index,
                variant_reason or "semantic_best_variant",
            )
            ctx.text = chosen

        ok, reason, missing = verify_meaning_preserved(
            ctx.source_text, baseline, ctx.text,
            target_lang=ctx.tgt_lang,
        )
        try:
            loss = float(compute_meaning_loss_score(
                ctx.source_text, baseline, ctx.text
            ))
        except Exception:
            loss = 0.0 if ok else 1.0
        score = max(0.0, 1.0 - loss)
        changed = ctx.text != original
        current_issues = detect_semantic_issues(
            ctx.source_text,
            ctx.text,
            source_lang=ctx.src_lang,
            target_lang=ctx.tgt_lang,
        )
        try:
            entity_score = float(compute_entity_preservation_score(ctx.source_text, ctx.text))
        except Exception:
            entity_score = 1.0
        if ok:
            return AgentResult(
                agent=self.name,
                text=ctx.text,
                changed=changed,
                ok=True,
                quality_score=score,
                reason=(
                    f"semantic_variant:{variant_reason}"
                    if changed
                    else f"meaning_ok:{reason}"
                ),
                diagnostics={
                    "input_data": {
                        "source_text": str(ctx.source_text or "")[:400],
                        "raw_translation": baseline[:400],
                        "incoming_text": original[:400],
                    },
                    "output_data": {
                        "text": ctx.text[:400],
                        "meaning_score": round(score, 3),
                        "entity_score": round(entity_score, 3),
                    },
                    "variants": variants,
                    "semantic_issues": current_issues,
                },
            )
        # Meaning lost → this is a translation fidelity problem.
        ctx.diagnostics["force_retranslate"] = True
        return AgentResult(
            agent=self.name, text=ctx.text, changed=changed, ok=False,
            quality_score=score, reason=f"meaning_loss:{reason}",
            route_back_to="translation",
            diagnostics={
                "missing": missing[:8],
                "input_data": {
                    "source_text": str(ctx.source_text or "")[:400],
                    "raw_translation": baseline[:400],
                    "incoming_text": original[:400],
                },
                "output_data": {"text": ctx.text[:400]},
                "variants": variants,
                "semantic_issues": current_issues,
                "errors": [f"meaning_loss:{reason}"],
            },
        )

    def _choose_semantic_variant(
        self, ctx: SegmentContext, *, baseline: str
    ) -> tuple[str, list[dict], str]:
        """Generate bounded semantic-only variants and choose the best one.

        This is intentionally NOT a timing or grammar optimizer. It only uses
        semantic-safe transformations already present in the repo.
        """
        from engines.repetition_guard import remove_repeated_sentences
        from engines.repetition_guard import has_repetition
        from engines.semantic_meaning import (
            apply_compact_phrases,
            compute_entity_preservation_score,
            compute_meaning_loss_score,
            verify_meaning_preserved,
        )
        from engines.semantic_translation import (
            apply_semantic_polish_line,
            detect_semantic_issues,
        )

        text = str(ctx.text or "").strip()
        if not text:
            return text, [], "empty"

        candidates: list[tuple[str, str]] = [("original", text)]
        deduped, deduped_changed = remove_repeated_sentences(text)
        if deduped_changed and deduped.strip():
            candidates.append(("dedupe_repetition", deduped.strip()))

        compact = apply_compact_phrases(text, target_lang=ctx.tgt_lang).strip()
        if compact and compact != text:
            candidates.append(("compact_phrases", compact))

        polished = apply_semantic_polish_line(text, target_lang=ctx.tgt_lang).strip()
        if polished and polished != text:
            candidates.append(("semantic_polish", polished))

        # Compose the two semantic-only rewrites if both changed text.
        if polished:
            polished_deduped, polished_deduped_changed = remove_repeated_sentences(polished)
            if polished_deduped_changed and polished_deduped.strip() and polished_deduped.strip() != polished:
                candidates.append(("semantic_polish+dedupe", polished_deduped.strip()))
            polished_compact = apply_compact_phrases(polished, target_lang=ctx.tgt_lang).strip()
            if polished_compact and polished_compact != polished:
                candidates.append(("semantic_polish+compact", polished_compact))

        seen: set[str] = set()
        variants: list[dict] = []
        best_text = text
        best_label = "original"
        best_score = (-1.0, -1.0, -1.0, 0.0)  # fewer issues, higher meaning/entity, not too short

        for label, cand in candidates:
            cand = " ".join(str(cand or "").split()).strip()
            if not cand or cand in seen:
                continue
            seen.add(cand)
            ok, reason, missing = verify_meaning_preserved(
                ctx.source_text, baseline, cand, target_lang=ctx.tgt_lang
            )
            try:
                meaning_loss = float(
                    compute_meaning_loss_score(ctx.source_text, baseline, cand)
                )
            except Exception:
                meaning_loss = 0.0 if ok else 1.0
            try:
                entity = float(compute_entity_preservation_score(ctx.source_text, cand))
            except Exception:
                entity = 1.0
            issues = detect_semantic_issues(
                ctx.source_text,
                cand,
                source_lang=ctx.src_lang,
                target_lang=ctx.tgt_lang,
            )
            repetition_penalty = 0 if has_repetition(cand) else 1
            meaning = max(0.0, 1.0 - meaning_loss) if ok else 0.0
            score = (
                repetition_penalty,      # duplicate meaning cleanup wins ties
                1.0 / (1 + len(issues)),  # fewer semantic issues is better
                meaning,
                entity,
                min(1.0, len(cand) / max(len(text), 1)),  # avoid collapsing too hard
            )
            variants.append(
                {
                    "label": label,
                    "text": cand[:400],
                    "ok": ok,
                    "reason": reason,
                    "missing": missing[:8],
                    "semantic_issue_count": len(issues),
                    "meaning_score": round(meaning, 3),
                    "entity_score": round(entity, 3),
                }
            )
            if ok and score > best_score:
                best_score = score
                best_text = cand
                best_label = label

        return best_text, variants, best_label


# ─────────────────────────────────────────────────────────────────────────────
# 4. Entity Agent
# ─────────────────────────────────────────────────────────────────────────────
class EntityAgent(Agent):
    """Verify entities/numbers/dates preserved; any loss = error → Translation."""

    name = "entity"

    _NUM_RE = None

    def _numbers(self, text: str) -> set[str]:
        import re

        if EntityAgent._NUM_RE is None:
            EntityAgent._NUM_RE = re.compile(r"\d+")
        return set(EntityAgent._NUM_RE.findall(str(text or "")))

    def _run(self, ctx: SegmentContext) -> AgentResult:
        from engines.semantic_meaning import compute_entity_preservation_score

        score = 1.0
        try:
            score = float(
                compute_entity_preservation_score(ctx.source_text, ctx.text)
            )
        except Exception:
            score = 1.0

        # Numbers/dates: every digit-run in the source must survive.
        src_nums = self._numbers(ctx.source_text)
        missing_nums = sorted(n for n in src_nums if n not in ctx.text)

        if score >= 0.999 and not missing_nums:
            return AgentResult(
                agent=self.name, text=ctx.text, changed=False, ok=True,
                quality_score=score, reason="entities_ok",
            )
        ctx.diagnostics["force_retranslate"] = True
        return AgentResult(
            agent=self.name, text=ctx.text, changed=False, ok=False,
            quality_score=score,
            reason="entity_loss" + (f":numbers={missing_nums}" if missing_nums else ""),
            route_back_to="translation",
            diagnostics={"entity_score": score, "missing_numbers": missing_nums},
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Timing Agent (MOST IMPORTANT)
# ─────────────────────────────────────────────────────────────────────────────
class TimingAgent(Agent):
    """Predict duration; if it fits do nothing, else reformulate to fit the slot.

    Reuses the existing reformulation-under-duration engine
    (:func:`adapt_segment_to_slot`) which itself tries rule-based compression
    first and only then an LLM rewrite (via the gateway), and hard-rejects any
    truncated / unfinished output.
    """

    name = "timing"

    def needed(self, ctx: SegmentContext) -> bool:
        if ctx.slot_ms <= 0 or not ctx.text.strip():
            return False
        try:
            from engines.semantic_optimizer import compute_time_budget

            budget = compute_time_budget(ctx.text, ctx.slot_ms, tgt_lang=ctx.tgt_lang)
        except Exception:
            return True
        if not budget.fits:
            return True
        # Fits but may be far too SHORT vs the source → allow natural expansion,
        # but only when the LLM can actually expand (never pad with fillers).
        if ctx.allow_llm and ctx.source_text.strip():
            try:
                from engines.semantic_adaptation import estimate_tts_duration_ms
                from engines.semantic_optimizer import EXPAND_TRIGGER_RATIO
                from engines.translation_adapt import llm_rephrase_available

                src_est = estimate_tts_duration_ms(
                    ctx.source_text, ctx.src_lang or ctx.tgt_lang
                )
                if (llm_rephrase_available()
                        and src_est > 0
                        and budget.tts_estimated_ms < int(src_est * EXPAND_TRIGGER_RATIO)):
                    return True
            except Exception:
                pass
        ctx.diagnostics["timing_budget_fits"] = True
        return False

    def _run(self, ctx: SegmentContext) -> AgentResult:
        from engines.timing_aware_translation import adapt_segment_to_slot

        before = str(ctx.text or "")
        ck = self.cache.key(
            ctx.text, ctx.slot_ms, ctx.src_lang, ctx.tgt_lang, ctx.model,
            ctx.quality_mode,
        )
        cached = self.cache.get(ck)
        if cached and cached.get("text"):
            new_text = cached["text"]
            changed = bool(cached.get("changed"))
            ctx.text = new_text
            return AgentResult(
                agent=self.name, text=new_text, changed=changed, cache_hit=True,
                quality_score=float(cached.get("slot_fit", 1.0)),
                reason=cached.get("reason", "timing_cached"),
                diagnostics={
                    "input_data": {
                        "incoming_text": before[:400],
                        "source_text": str(ctx.source_text or "")[:400],
                        "slot_ms": int(ctx.slot_ms or 0),
                    },
                    "output_data": {
                        "text": str(new_text or "")[:400],
                        "reason": cached.get("reason", "timing_cached"),
                    },
                },
            )

        new_text, record = adapt_segment_to_slot(
            ctx.text,
            source_text=ctx.source_text,
            slot_ms=ctx.slot_ms,
            src_lang=ctx.src_lang,
            tgt_lang=ctx.tgt_lang,
            index=ctx.index,
        )
        ctx.timing_record = record
        ctx.text = new_text or ctx.text
        handoff_reason = record.reason or "timing_done"

        # AI Core Timing Agent is the text-length owner. If the lower-level
        # adaptation engine could not safely finish the rewrite, we still hand
        # off a completed best-effort text to downstream non-text stages
        # (punctuation/stress/validation/video-adapt), instead of leaving the
        # segment open for legacy text adaptation to retry again.
        if record.requires_llm_adaptation or "requires_llm" in str(handoff_reason):
            reason_lower = str(record.reason or "").lower()
            lang_issue = any(
                token in reason_lower
                for token in (
                    "language",
                    "mismatch",
                    "english",
                    "translit",
                    "target_lang",
                    "uk_track",
                    "ru_track",
                )
            )
            if not lang_issue:
                trace = dict(record.ai_adaptation_trace or {})
                trace["timing_unresolved_reason"] = str(record.reason or "")
                trace["timing_handoff_strategy"] = "video_adapt"
                record.ai_adaptation_trace = trace
                record.requires_llm_adaptation = False
                record.reason = "video_adapt_required"
                handoff_reason = record.reason

        slot_fit = self._slot_fit(new_text, ctx.slot_ms, ctx.tgt_lang)
        self.cache.put(ck, {
            "text": ctx.text, "changed": bool(record.adapted),
            "slot_fit": slot_fit, "reason": handoff_reason,
        })
        return AgentResult(
            agent=self.name, text=ctx.text, changed=bool(record.adapted),
            quality_score=slot_fit, used_llm=bool(record.llm_called),
            reason=handoff_reason,
            diagnostics={
                "input_data": {
                    "incoming_text": before[:400],
                    "source_text": str(ctx.source_text or "")[:400],
                    "slot_ms": int(ctx.slot_ms or 0),
                },
                "output_data": {
                    "text": str(ctx.text or "")[:400],
                    "reason": handoff_reason,
                    "predicted_ms_after": record.predicted_ms_after,
                },
                "model": (record.ai_adaptation_trace or {}).get("model", ""),
                "predicted_ms_after": record.predicted_ms_after,
                "slot_ms": record.slot_ms,
                "timing_handoff_strategy": (
                    (record.ai_adaptation_trace or {}).get("timing_handoff_strategy") or ""
                ),
            },
        )

    @staticmethod
    def _slot_fit(text: str, slot_ms: int, tgt_lang: str) -> float:
        try:
            from engines.semantic_optimizer import compute_time_budget

            b = compute_time_budget(text, slot_ms, tgt_lang=tgt_lang)
            if b.fits:
                return 1.0
            if b.target_ms <= 0:
                return 1.0
            over = b.tts_estimated_ms / max(1, b.target_ms)
            return max(0.0, min(1.0, 2.0 - over))
        except Exception:
            return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Grammar Agent
# ─────────────────────────────────────────────────────────────────────────────
class GrammarAgent(Agent):
    """Fix only grammar/punctuation/naturalness; never change meaning."""

    name = "grammar"

    def needed(self, ctx: SegmentContext) -> bool:
        return self._needs_polish(ctx.text)

    def _run(self, ctx: SegmentContext) -> AgentResult:
        before = str(ctx.text or "")
        ck = self.cache.key(before, ctx.tgt_lang, ctx.model)
        cached = self.cache.get(ck)
        if cached and cached.get("text"):
            ctx.text = cached["text"]
            return AgentResult(
                agent=self.name, text=ctx.text, changed=(ctx.text != before),
                cache_hit=True, reason="grammar_cached",
                diagnostics={
                    "input_data": {
                        "incoming_text": before[:400],
                        "source_text": str(ctx.source_text or "")[:400],
                    },
                    "output_data": {
                        "text": str(ctx.text or "")[:400],
                        "timing_preserved": True,
                    },
                },
            )

        polished = self._rule_polish(before, ctx.tgt_lang)
        if polished and not self._timing_safe(ctx, polished):
            polished = before

        # Only reach for the LLM if rule polish still leaves grammar/naturalness
        # issues and the strategy permits it (cheap-first).
        used_llm = False
        model = ""
        still_needs = self._needs_polish(polished)
        if still_needs and ctx.allow_llm and ctx.llm_policy != "off":
            try:
                from engines.translation_adapt import (
                    MODE_FAST,
                    adaptation_speed_mode,
                    _is_cpu_only,
                )

                if _is_cpu_only() and adaptation_speed_mode() == MODE_FAST:
                    still_needs = False
            except Exception:
                pass
        if still_needs and ctx.allow_llm and ctx.llm_policy != "off":
            llm_text, model = self._llm_polish(polished, ctx)
            if (
                llm_text
                and self._meaning_safe(ctx, llm_text)
                and self._surface_safe(ctx, llm_text)
                and self._timing_safe(ctx, llm_text)
            ):
                polished = llm_text
                used_llm = True

        changed = polished.strip() != before.strip()
        if changed:
            ctx.text = polished
        self.cache.put(ck, {"text": ctx.text})
        return AgentResult(
            agent=self.name, text=ctx.text, changed=changed, used_llm=used_llm,
            reason="grammar_polished" if changed else "grammar_ok",
            diagnostics={
                "input_data": {
                    "incoming_text": before[:400],
                    "source_text": str(ctx.source_text or "")[:400],
                },
                "output_data": {
                    "text": str(ctx.text or "")[:400],
                    "timing_preserved": True,
                },
                **({"model": model} if used_llm else {}),
            },
        )

    @staticmethod
    def _needs_polish(text: str) -> bool:
        from engines.sentence_integrity import validate_tts_text

        t = str(text or "")
        ok, _issues = validate_tts_text(t)
        if not ok:
            return True
        try:
            from engines.naturalizer_v2.bad_patterns import has_bad_mt

            if has_bad_mt(t):
                return True
        except Exception:
            pass
        stripped = t.strip()
        if stripped and stripped[-1] not in ".!?…»\"')":
            return True
        return False

    @staticmethod
    def _rule_polish(text: str, tgt_lang: str) -> str:
        lang = str(tgt_lang or "").strip().lower()[:2]
        try:
            from engines import translation_naturalizer as tn

            if lang == "ru":
                return tn.naturalize_ru(text)
            if lang == "uk":
                return tn.naturalize_uk(text)
            return tn.naturalize_generic(text)
        except Exception:
            return text

    @staticmethod
    def _llm_polish(text: str, ctx: SegmentContext) -> tuple[str, str]:
        from engines.ai_core import llm_gateway

        lang = ctx.tgt_lang or "the same language"
        prompt = (
            f"Fix ONLY grammar, punctuation and naturalness of this {lang} line. "
            f"Do NOT change the meaning, do not add or remove information, keep all "
            f"names and numbers. Return ONLY the corrected line.\n\n{text}"
        )
        out = llm_gateway.chat(prompt, max_tokens=256, temperature=0.1)
        return str(out or "").strip().strip('"').strip(), llm_gateway.active_model()

    @staticmethod
    def _meaning_safe(ctx: SegmentContext, candidate: str) -> bool:
        if not candidate:
            return False
        try:
            from engines.semantic_meaning import verify_meaning_preserved

            ok, _r, _m = verify_meaning_preserved(
                ctx.source_text, ctx.text, candidate, target_lang=ctx.tgt_lang
            )
            return ok
        except Exception:
            return False

    @staticmethod
    def _surface_safe(ctx: SegmentContext, candidate: str) -> bool:
        if not candidate:
            return False
        try:
            from engines.pipeline_language_gate import is_critical_language_mismatch
            from engines.sentence_integrity import validate_tts_text

            ok, _issues = validate_tts_text(candidate)
            if not ok:
                return False
            bad, _ = is_critical_language_mismatch(
                candidate,
                target_lang=ctx.tgt_lang,
                original=ctx.source_text,
            )
            return not bad
        except Exception:
            return False

    @staticmethod
    def _timing_safe(ctx: SegmentContext, candidate: str) -> bool:
        try:
            from engines.semantic_optimizer import compute_time_budget
            from engines.timing_aware_translation import word_count
        except Exception:
            return True
        before = str(ctx.text or "")
        after = str(candidate or "")
        if not before.strip() or not after.strip():
            return False
        before_words = word_count(before)
        after_words = word_count(after)
        if abs(after_words - before_words) > max(2, int(before_words * 0.2)):
            return False
        if int(ctx.slot_ms or 0) <= 0:
            return True
        before_budget = compute_time_budget(before, ctx.slot_ms, tgt_lang=ctx.tgt_lang)
        after_budget = compute_time_budget(after, ctx.slot_ms, tgt_lang=ctx.tgt_lang)
        if before_budget.fits and not after_budget.fits:
            return False
        if after_budget.delta_ms > before_budget.delta_ms + 250:
            return False
        return True


# ─────────────────────────────────────────────────────────────────────────────
# 7. Quality Agent
# ─────────────────────────────────────────────────────────────────────────────
class QualityAgent(Agent):
    """Gate before TTS; route failures back to ONLY the responsible agent."""

    name = "quality"

    # Map a failing check to the single agent responsible for fixing it.
    _RESPONSIBLE = {
        "meaning": "semantic",
        "entity": "entity",
        "language_mismatch": "translation",
        "timing_overflow": "timing",
        # integrity / grammar issues → Grammar Agent
        "empty": "grammar",
        "mid_word": "grammar",
        "incomplete_sentence": "grammar",
        "dangling_connector": "grammar",
        "too_short": "grammar",
        "repeats": "grammar",
        "repetition": "grammar",
        "null_sentinel": "grammar",
        "bad_mt": "grammar",
    }

    def _run(self, ctx: SegmentContext) -> AgentResult:
        from engines.ai_adaptation_engine import validate_pre_tts_checks

        ok, issues = validate_pre_tts_checks(
            ctx.text,
            source_hint=ctx.source_text,
            original=ctx.raw_translation or ctx.text,
            slot_ms=ctx.slot_ms,
            tgt_lang=ctx.tgt_lang,
        )
        score = self._score(ctx, issues)
        if ok:
            return AgentResult(
                agent=self.name, text=ctx.text, changed=False, ok=True,
                quality_score=score, reason="quality_pass",
                diagnostics={
                    "input_data": {
                        "incoming_text": str(ctx.text or "")[:400],
                        "source_text": str(ctx.source_text or "")[:400],
                        "slot_ms": int(ctx.slot_ms or 0),
                    },
                    "output_data": {
                        "text": str(ctx.text or "")[:400],
                        "issues": [],
                    },
                },
            )

        responsible = self._route(issues)
        return AgentResult(
            agent=self.name, text=ctx.text, changed=False, ok=False,
            quality_score=score, reason="quality_fail:" + ",".join(issues[:4]),
            route_back_to=responsible,
            diagnostics={
                "issues": issues,
                "input_data": {
                    "incoming_text": str(ctx.text or "")[:400],
                    "source_text": str(ctx.source_text or "")[:400],
                    "slot_ms": int(ctx.slot_ms or 0),
                },
                "output_data": {
                    "text": str(ctx.text or "")[:400],
                    "issues": issues[:8],
                    "route_back_to": responsible,
                },
            },
        )

    def _route(self, issues: list[str]) -> str | None:
        priority = [
            "meaning",
            "entity",
            "language_mismatch",
            "timing_overflow",
            "empty",
            "mid_word",
            "incomplete_sentence",
            "dangling_connector",
            "too_short",
            "repeats",
            "repetition",
            "null_sentinel",
            "bad_mt",
        ]
        heads = [issue.split(":", 1)[0] for issue in issues]
        for wanted in priority:
            if wanted in heads:
                return self._RESPONSIBLE.get(wanted)
        for head in heads:
            agent = self._RESPONSIBLE.get(head)
            if agent:
                return agent
        return None

    @staticmethod
    def _score(ctx: SegmentContext, issues: list[str]) -> float:
        if not issues:
            return 1.0
        # Each outstanding issue costs a fixed penalty (bounded at 0).
        return max(0.0, 1.0 - 0.2 * len(issues))
