"""TQE Pipeline — Chain of Responsibility of independent Reviewers."""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from engines.tqe.analytics import (
    build_analytics,
    check_regression,
    persist_accepted,
    persist_batch_report,
    persist_failure,
)
from engines.tqe.decision import decide_segment
from engines.tqe.explain import explain_decision
from engines.tqe.models import ReviewStatus, TQEBatchResult
from engines.tqe.retry import RetryManager
from engines.tqe.reviewers import (
    EntityReviewer,
    FastQAReviewer,
    GrammarReviewer,
    LLMJudgeReviewer,
    MeaningReviewer,
    NarrativeReviewer,
    TimingReviewer,
)
from engines.tqe.reviewers.base import BaseReviewer

logger = logging.getLogger("tubedub.tqe.pipeline")


def default_reviewers(*, include_llm: bool | None = None) -> list[BaseReviewer]:
    reviewers: list[BaseReviewer] = [
        FastQAReviewer(),
        EntityReviewer(),
        MeaningReviewer(),
        GrammarReviewer(),
        TimingReviewer(),
        NarrativeReviewer(),
    ]
    use_llm = include_llm
    if use_llm is None:
        use_llm = os.getenv("TQE_LLM_JUDGE", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if use_llm:
        reviewers.append(LLMJudgeReviewer())
    return reviewers


def run_tqe_on_segments(
    *,
    task_id: str,
    originals: list[str],
    translations: list[str],
    timing_map: list[dict] | None = None,
    app_dir: str | None = None,
    reviewers: Iterable[BaseReviewer] | None = None,
    confidence_threshold: float | None = None,
    persist: bool = True,
    allow_retry: bool = True,
    src_lang: str | None = None,
    tgt_lang: str | None = None,
) -> TQEBatchResult:
    """Run full TQE pipeline. Segments that REJECT are not allowed for TTS."""
    from pathlib import Path

    base = Path(app_dir) if app_dir else Path(__file__).resolve().parents[2]
    chain = list(reviewers) if reviewers is not None else default_reviewers()
    retry_mgr = RetryManager()
    n = max(len(originals), len(translations))
    decisions = []
    explanations: list[str] = []
    blocked: list[int] = []

    # Infer langs from first non-empty pair when caller omitted them
    src_l = (src_lang or "").split("-")[0].lower()
    tgt_l = (tgt_lang or "").split("-")[0].lower()
    if not src_l or not tgt_l:
        try:
            from engines.mt.cross_script_guard import dominant_script, expected_script

            sample_src = next((str(x) for x in originals if str(x or "").strip()), "")
            sample_tgt = next((str(x) for x in translations if str(x or "").strip()), "")
            if not src_l:
                ds = dominant_script(sample_src)
                src_l = {"cjk": "zh", "latin": "en", "cyrillic": "uk"}.get(ds or "", "en")
            if not tgt_l:
                ds = dominant_script(sample_tgt)
                tgt_l = {"cyrillic": "uk", "latin": "en", "cjk": "zh"}.get(ds or "", "uk")
            _ = expected_script  # silence lint if unused in some builds
        except Exception:
            src_l = src_l or "en"
            tgt_l = tgt_l or "uk"

    prev_tr = ""
    for i in range(n):
        original = str(originals[i] if i < len(originals) else "")
        translation = str(translations[i] if i < len(translations) else "")
        slot_ms = 0
        if timing_map and i < len(timing_map):
            row = timing_map[i] or {}
            try:
                start = int(row.get("start") or row.get("start_ms") or 0)
                end = int(row.get("end") or row.get("end_ms") or 0)
                slot_ms = max(0, end - start)
            except Exception:
                slot_ms = 0

        reports = []
        prior_critical: list[dict] = []
        prior_errors: list[dict] = []
        for rev in chain:
            # Short-circuit LLM when critical already found
            ctx = {
                "slot_ms": slot_ms,
                "prev_translation": prev_tr,
                "prior_critical_errors": prior_critical,
                "prior_errors": prior_errors,
                "task_id": task_id,
                "source_lang": src_l,
                "target_lang": tgt_l,
            }
            if rev.name == "LLMJudgeReviewer" and prior_critical:
                from engines.tqe.models import QualityReport

                reports.append(
                    QualityReport(
                        reviewer_name=rev.name,
                        status=ReviewStatus.SKIP,
                        explanation="skipped_due_to_prior_critical",
                        metadata={"index": i},
                    )
                )
                continue
            report = rev.review(
                index=i, original=original, translation=translation, context=ctx
            )
            reports.append(report)
            prior_errors.extend(report.errors)
            if report.status == ReviewStatus.REJECT:
                prior_critical.extend(
                    [e for e in report.errors if e.get("severity") == "critical"]
                    or report.errors
                )

        decision = decide_segment(
            index=i,
            original=original,
            translation=translation,
            reports=reports,
            threshold=confidence_threshold,
        )

        retry_history: list[dict] = []
        if (
            allow_retry
            and not decision.allowed_for_tts
            and decision.retry_strategy != "none"
        ):
            # 1) Prefer offline sentence-level Argos repair for meaning collapse
            repaired = ""
            try:
                from engines.mt.argos_engine import ArgosEngine
                from engines.mt.cross_script_guard import (
                    meaning_collapse,
                    source_script_leak,
                    strip_source_script_chars,
                )
                from engines.mt.sentence_split import (
                    is_severe_mt_collapse,
                    split_mt_sentences,
                )

                need_repair = is_severe_mt_collapse(original, translation) or any(
                    e.get("code")
                    in (
                        "severe_truncation",
                        "meaning_collapse",
                        "cjk_meaning_collapse",
                        "narrative_drop",
                        "source_script_leak",
                        "dirty_mt_noop",
                        "phrase_loop",
                    )
                    for r in decision.reports
                    for e in r.errors
                )
                if need_repair:
                    eng = ArgosEngine()
                    pieces = []
                    for sent in split_mt_sentences(original):
                        r = eng.translate(sent, src_l or "en", tgt_l or "uk")
                        piece = str(r.text or "").strip()
                        pieces.append(piece if piece else sent)
                    candidate = " ".join(pieces).strip()
                    candidate = (
                        strip_source_script_chars(
                            candidate, source_lang=src_l, source=original
                        )
                        or candidate
                    )

                    def _argos_ok(cand: str) -> bool:
                        if not cand:
                            return False
                        if source_script_leak(
                            original, cand, source_lang=src_l, target_lang=tgt_l
                        ):
                            return False
                        if meaning_collapse(
                            original, cand, source_lang=src_l, target_lang=tgt_l
                        ):
                            return False
                        if is_severe_mt_collapse(original, cand):
                            return False
                        return True

                    if _argos_ok(candidate) and (
                        not is_severe_mt_collapse(original, candidate)
                        or len(candidate.split()) > len(translation.split()) * 1.5
                    ):
                        repaired = candidate
                        translation = candidate
                        retry_history.append(
                            {
                                "ok": True,
                                "strategy": "argos_sentence_retranslate",
                                "text": repaired,
                            }
                        )
            except Exception as exc:
                retry_history.append({"ok": False, "reason": f"argos_repair:{exc}"})

            # 1b) CJK→uk/ru: LLM direct from source when Argos cannot recover
            if not repaired:
                try:
                    from engines.mt.llm_retranslate import (
                        llm_direct_translate,
                        should_llm_retranslate,
                    )

                    if should_llm_retranslate(src_lang=src_l, tgt_lang=tgt_l):
                        llm_cand = llm_direct_translate(
                            original,
                            src_lang=src_l or "zh",
                            tgt_lang=tgt_l or "uk",
                            task_id=task_id,
                            segment_idx=i,
                        )
                        if llm_cand:
                            repaired = llm_cand
                            translation = llm_cand
                            retry_history.append(
                                {
                                    "ok": True,
                                    "strategy": "llm_direct_retranslate",
                                    "text": repaired,
                                }
                            )
                except Exception as exc:
                    retry_history.append(
                        {"ok": False, "reason": f"llm_direct_repair:{exc}"}
                    )

            # 2) Optional LLM strategy when Argos/LLM-direct repair did not help
            if not repaired:
                try:
                    from engines.llm_adaptation_mode import chat_completion

                    def _llm(prompt: str) -> str:
                        return str(
                            chat_completion(
                                prompt,
                                system="Fix translation quality issues. Return only target text.",
                                temperature=0.1,
                                max_tokens=max(800, len(original.split()) * 4),
                            )
                            or ""
                        )

                    retry_out = retry_mgr.apply_once(decision, llm_fn=_llm)
                    retry_history.append(retry_out)
                    if retry_out.get("ok") and retry_out.get("text"):
                        translation = str(retry_out["text"])
                        repaired = translation
                except Exception as exc:
                    retry_history.append({"ok": False, "reason": str(exc)})

            if repaired:
                reports2 = []
                prior_critical = []
                prior_errors = []
                for rev in chain:
                    if rev.name == "LLMJudgeReviewer":
                        continue
                    ctx = {
                        "slot_ms": slot_ms,
                        "prev_translation": prev_tr,
                        "prior_critical_errors": prior_critical,
                        "prior_errors": prior_errors,
                        "task_id": task_id,
                        "retry": True,
                        "source_lang": src_l,
                        "target_lang": tgt_l,
                    }
                    report = rev.review(
                        index=i,
                        original=original,
                        translation=translation,
                        context=ctx,
                    )
                    report.retry_count = 1
                    reports2.append(report)
                    prior_errors.extend(report.errors)
                    if report.status == ReviewStatus.REJECT:
                        prior_critical.extend(report.errors)
                decision = decide_segment(
                    index=i,
                    original=original,
                    translation=translation,
                    reports=reports2,
                    threshold=confidence_threshold,
                )

        exp = explain_decision(decision)
        explanations.append(exp)
        if not decision.allowed_for_tts:
            blocked.append(i)
            if persist:
                try:
                    persist_failure(
                        base,
                        decision,
                        task_id=task_id,
                        retry_history=retry_history,
                    )
                except Exception as exc:
                    logger.debug("persist failure skipped: %s", exc)
        elif persist:
            try:
                persist_accepted(base, decision, task_id=task_id)
            except Exception:
                pass

        decisions.append(decision)
        prev_tr = decision.translation

    passed = sum(1 for d in decisions if d.allowed_for_tts)
    rejected = len(decisions) - passed
    overall = (
        round(sum(d.overall_confidence for d in decisions) / max(len(decisions), 1), 4)
        if decisions
        else 0.0
    )
    # Gate passes only when EVERY segment is allowed
    gate_passed = rejected == 0 and len(decisions) > 0
    result = TQEBatchResult(
        task_id=task_id,
        decisions=decisions,
        passed=passed,
        rejected=rejected,
        overall_confidence=overall,
        gate_passed=gate_passed,
        blocked_indices=blocked,
        explanations=explanations,
    )
    result.analytics = build_analytics(result)
    if persist:
        try:
            persist_batch_report(base, result)
            reg = check_regression(base, result.analytics)
            result.analytics["regression"] = reg
            if reg.get("regressed"):
                logger.warning(
                    "[TQE] quality regression detected task=%s drops=%s",
                    task_id,
                    reg.get("drops"),
                )
        except Exception as exc:
            logger.debug("TQE persist/analytics skipped: %s", exc)
    return result


def filter_tts_texts(
    translations: list[str],
    result: TQEBatchResult,
    *,
    blank_rejected: bool = True,
) -> list[str]:
    """Return texts safe for TTS; rejected segments become empty if blank_rejected."""
    out = list(translations)
    for d in result.decisions:
        if d.index >= len(out):
            continue
        if d.allowed_for_tts:
            out[d.index] = d.translation
        elif blank_rejected:
            out[d.index] = ""
    return out
