"""TPS pipeline — Fast Path / Retry(1) / LLM Judge / Manual Review (TPS4)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.tps.approved_text import approve_segment, sync_audits_approved
from engines.tps.fast_qa import run_fast_qa
from engines.tps.metrics import TPSMetrics, write_tps_metrics
from engines.tps.owners import clear_owner_registry, get_owner_registry
from engines.tps.statuses import TPSPath, TQEStatus

logger = logging.getLogger("tubedub.tps.pipeline")


@dataclass
class TPSSegmentResult:
    index: int
    status: TQEStatus
    path: TPSPath
    text: str
    original: str
    reason_codes: list[str] = field(default_factory=list)
    llm_calls: int = 0
    elapsed_ms: float = 0.0
    needs_manual_review: bool = False


@dataclass
class TPSBatchResult:
    task_id: str
    segments: list[TPSSegmentResult] = field(default_factory=list)
    metrics: TPSMetrics = field(default_factory=TPSMetrics)
    texts: list[str] = field(default_factory=list)
    manual_indices: list[int] = field(default_factory=list)
    gate_passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "gate_passed": self.gate_passed,
            "manual_indices": list(self.manual_indices),
            "metrics": self.metrics.to_dict(),
            "segments": [
                {
                    "index": s.index,
                    "status": s.status.value,
                    "path": s.path.value,
                    "text": s.text,
                    "reason_codes": s.reason_codes,
                    "llm_calls": s.llm_calls,
                    "elapsed_ms": s.elapsed_ms,
                    "needs_manual_review": s.needs_manual_review,
                }
                for s in self.segments
            ],
        }


def _tps_naturalizer_use_llm() -> bool:
    """LLM rewrite for dirty/bad MT. Default OFF (engine-first: MT + rules)."""
    import os

    try:
        from engines.llm_kill_switch import is_heavy_llm_disabled

        if is_heavy_llm_disabled():
            return False
    except Exception:
        pass
    mode = os.getenv("TPS_NATURALIZER_LLM", "off").strip().lower()
    if mode in ("0", "false", "no", "off"):
        return False
    if mode in ("1", "true", "yes", "on"):
        return True
    # auto
    try:
        from engines.ai_core import llm_gateway

        return bool(llm_gateway.is_available())
    except Exception:
        return False


def _slot_ms_from_seg(seg: dict[str, Any] | None) -> int:
    if not isinstance(seg, dict):
        return 0
    try:
        slot = int(seg.get("slot_ms") or 0)
    except (TypeError, ValueError):
        slot = 0
    if slot > 0:
        return slot
    try:
        s = int(seg.get("start_ms") or seg.get("start") or 0)
        e = int(seg.get("end_ms") or seg.get("end") or 0)
        if e > s:
            return e - s
    except (TypeError, ValueError):
        pass
    return 0


def _rule_naturalize(
    texts: list[str],
    sources: list[str],
    *,
    src_lang: str,
    tgt_lang: str,
    task_id: str,
    app_dir: str | Path | None = None,
    slot_ms_list: list[int] | None = None,
    reserve_ms_list: list[int] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Naturalizer rule-first; LLM rewrite when dirty MT / bad quality (TPS)."""
    meta_out: list[dict[str, Any]] = []
    reasons_out: list[list[str]] = []
    try:
        from engines.translation_naturalizer import polish_lines

        owners = get_owner_registry(task_id)
        for i in range(len(texts)):
            try:
                owners.claim("naturalize", "Naturalizer", segment_index=i)
            except Exception:
                pass
        use_llm = _tps_naturalizer_use_llm()
        polished = polish_lines(
            list(texts),
            source_segments=list(sources),
            tgt_lang=tgt_lang,
            src_lang=src_lang,
            use_llm=use_llm,
            app_dir=app_dir,
            naturalizer_meta_out=meta_out,
            naturalizer_reasons_out=reasons_out,
            slot_ms_list=list(slot_ms_list) if slot_ms_list else None,
            reserve_ms_list=list(reserve_ms_list) if reserve_ms_list else None,
        )
        # Attach reasons into meta for TRH
        for i, m in enumerate(meta_out):
            if i < len(reasons_out):
                m["reasons"] = list(reasons_out[i])
                m.setdefault(
                    "naturalizer_applied",
                    (polished[i] if i < len(polished) else "") != (texts[i] if i < len(texts) else ""),
                )
                m["llm_enabled"] = use_llm
        while len(meta_out) < len(texts):
            meta_out.append({"naturalizer_called": True, "naturalizer_skip_reason": "no_meta"})
        return polished, meta_out
    except Exception as exc:
        logger.debug("rule naturalize skipped: %s", exc)
        return list(texts), [
            {
                "naturalizer_called": False,
                "naturalizer_skip_reason": f"exception:{exc}",
                "naturalizer_applied": False,
            }
            for _ in texts
        ]


def _retry_meaning_grammar(
    original: str,
    text: str,
    *,
    src_lang: str,
    tgt_lang: str,
    reason_codes: list[str],
) -> tuple[str, int]:
    """Exactly one retry pass — route to responsible owner by reason."""
    llm_calls = 0
    out = text
    codes = set(reason_codes)

    def _llm_cjk_rescue(candidate_src: str, current: str) -> tuple[str, int]:
        """LLM direct from source for CJK→uk/ru when polish cannot recover meaning."""
        calls = 0
        try:
            from engines.mt.cross_script_guard import (
                meaning_collapse,
                source_script_leak,
                strip_source_script_chars,
            )
            from engines.mt.llm_retranslate import (
                llm_direct_translate,
                should_llm_retranslate,
            )

            if not should_llm_retranslate(src_lang=src_lang, tgt_lang=tgt_lang):
                return current, 0
            llm_cand = llm_direct_translate(
                candidate_src,
                src_lang=src_lang or "zh",
                tgt_lang=tgt_lang or "uk",
            )
            if not llm_cand:
                return current, 0
            calls = 1
            cleaned = strip_source_script_chars(
                llm_cand, source_lang=src_lang, source=candidate_src
            ) or llm_cand
            if source_script_leak(
                candidate_src, cleaned, source_lang=src_lang, target_lang=tgt_lang
            ):
                return current, calls
            if meaning_collapse(
                candidate_src, cleaned, source_lang=src_lang, target_lang=tgt_lang
            ):
                # Never ship still-collapsed LLM output — even when current is a loop.
                # Accepting collapsed "cleaned" led to PASS+CJK approved (_tmp_3333).
                return current, calls
            return cleaned, calls
        except Exception as exc:
            logger.debug("cjk llm rescue failed: %s", exc)
            return current, calls

    # TRH: dirty_mt_noop / entity_breakage / en_word_leak → force re-naturalize + canon
    if codes & {
        "dirty_mt_noop",
        "entity_missing",
        "entity_breakage",
        "mixed_language",
        "english_leak",
        "en_word_leak",
        "nonsense_calque",
        "phrase_loop",
    }:
        try:
            from engines.mt.dirty_mt import (
                apply_temporary_entity_repair,
                residual_dirty_after_naturalize,
            )
            from engines.trh.canon_repair import apply_canon_repair
            from engines.translation_naturalizer import polish_lines

            repaired, _tickets = apply_temporary_entity_repair(out)
            repaired, _t2 = apply_canon_repair(
                repaired, original=original, tgt_lang=tgt_lang
            )
            polished = polish_lines(
                [repaired],
                source_segments=[original],
                tgt_lang=tgt_lang,
                src_lang=src_lang,
                use_llm=_tps_naturalizer_use_llm(),
            )
            if polished and polished[0].strip():
                out = polished[0].strip()
            out, _t3 = apply_canon_repair(out, original=original, tgt_lang=tgt_lang)
            if _tps_naturalizer_use_llm():
                llm_calls += 1
            # dirty_mt_noop on CJK: polish alone cannot recover — LLM from source
            if residual_dirty_after_naturalize(
                original, out, tgt_lang=tgt_lang
            ) or "dirty_mt_noop" in codes:
                rescued, c = _llm_cjk_rescue(original, out)
                llm_calls += c
                # _llm_cjk_rescue already rejects leak/collapse; accept only if changed.
                if rescued and rescued != out:
                    out = rescued
        except Exception as exc:
            logger.debug("dirty_mt retry polish failed: %s", exc)

    # Meaning / collapse / CJK leak → Argos sentence retry, then LLM direct (zh→uk/ru)
    if codes & {
        "meaning_loss",
        "severe_truncation",
        "meaning_collapse",
        "empty",
        "source_script_leak",
        "cjk_meaning_collapse",
        "phrase_loop",
    }:
        try:
            from engines.mt.argos_engine import ArgosEngine
            from engines.mt.cross_script_guard import (
                has_phrase_loop,
                meaning_collapse,
                source_script_leak,
                strip_source_script_chars,
            )
            from engines.mt.sentence_split import (
                is_severe_mt_collapse,
                split_mt_sentences,
            )

            eng = ArgosEngine()
            pieces = []
            for sent in split_mt_sentences(original):
                r = eng.translate(sent, src_lang or "en", tgt_lang or "uk")
                piece = str(r.text or "").strip()
                pieces.append(piece if piece else sent)
            candidate = " ".join(pieces).strip()

            def _retry_ok(cand: str) -> bool:
                if not cand:
                    return False
                cleaned = strip_source_script_chars(
                    cand, source_lang=src_lang, source=original
                )
                check = cleaned or cand
                if source_script_leak(
                    original, check, source_lang=src_lang, target_lang=tgt_lang
                ):
                    return False
                if meaning_collapse(
                    original, check, source_lang=src_lang, target_lang=tgt_lang
                ):
                    return False
                if has_phrase_loop(check):
                    return False
                if is_severe_mt_collapse(original, check):
                    return False
                return True

            if _retry_ok(candidate):
                out = (
                    strip_source_script_chars(
                        candidate, source_lang=src_lang, source=original
                    )
                    or candidate
                )
            else:
                rescued, c = _llm_cjk_rescue(original, out)
                llm_calls += c
                if rescued and _retry_ok(rescued):
                    out = rescued
                # Else keep prior text — do not accept flower/collapsed hallucinations
            # Else keep prior text — do not accept flower-delivery hallucinations
        except Exception as exc:
            logger.debug("meaning retry failed: %s", exc)

    # Grammar / incomplete → light rule polish only (no LLM on retry by default)
    if codes & {
        "incomplete",
        "incomplete_sentence",
        "orphan_clause_glue",
        "orphan_clause_prefix",
        "grammar",
    }:
        try:
            from engines.dsal.pre_lock_polish import polish_double_punctuation, polish_false_name_period

            out = polish_false_name_period(polish_double_punctuation(out))
        except Exception:
            try:
                from engines.translation_naturalizer import polish_lines

                out = polish_lines(
                    [out],
                    source_segments=[original],
                    tgt_lang=tgt_lang,
                    src_lang=src_lang,
                    use_llm=_tps_naturalizer_use_llm(),
                )[0]
            except Exception:
                pass

    # Entity missing → try preserve tokens
    if "entity_missing" in codes or "preserved_token" in codes:
        try:
            from engines.translation_quality import preserve_preserved_tokens

            out = preserve_preserved_tokens(original, out)
        except Exception:
            pass

    return out, llm_calls


def _llm_judge(
    original: str,
    text: str,
    *,
    errors: list[dict],
    tgt_lang: str,
) -> tuple[str, bool, int]:
    """One judge pass — confirm or one corrected candidate. Never infinite."""
    import json
    import os

    # Default ON for FAIL_RETRY path; disable with TPS_LLM_JUDGE=0 / TQE_LLM_JUDGE=0
    _judge_env = os.getenv("TPS_LLM_JUDGE", os.getenv("TQE_LLM_JUDGE", "1")).strip().lower()
    if _judge_env in ("0", "false", "no", "off"):
        return text, False, 0
    if _judge_env not in ("1", "true", "yes", "on", ""):
        # Unknown value — treat as enabled (safe default for quality)
        pass
    try:
        from engines.llm_adaptation_mode import chat_completion

        prompt = (
            "You are a translation quality judge (not a free translator).\n"
            "Return ONLY JSON: {\"verdict\":\"PASS\"|\"FAIL\",\"text\":\"...\"}\n"
            "If FAIL, put one corrected translation in text; if PASS, repeat input text.\n"
            f"Target language: {tgt_lang}\n"
            f"Original: {original}\n"
            f"Translation: {text}\n"
            f"Errors: {json.dumps(errors, ensure_ascii=False)}\n"
        )
        raw = str(
            chat_completion(
                prompt,
                system="Judge translation quality. Preserve all facts and entities.",
                temperature=0.0,
                max_tokens=max(600, len(original.split()) * 4),
            )
            or ""
        ).strip()
        data = {}
        try:
            data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
        except Exception:
            data = {"verdict": "FAIL" if "FAIL" in raw.upper() else "PASS", "text": text}
        verdict = str(data.get("verdict") or "FAIL").upper()
        candidate = str(data.get("text") or text).strip() or text
        return candidate, verdict == "PASS", 1
    except Exception as exc:
        logger.debug("LLM judge unavailable: %s", exc)
        return text, False, 0


def run_tps_pipeline(
    *,
    task_id: str,
    originals: list[str],
    translations: list[str],
    src_lang: str = "en",
    tgt_lang: str = "uk",
    app_dir: str | Path | None = None,
    session_dir: str | Path | None = None,
    info: dict[str, Any] | None = None,
    persist_metrics: bool = True,
) -> TPSBatchResult:
    """MT texts in → Approved texts out via Fast/Retry(1)/Judge/Manual."""
    clear_owner_registry(task_id)
    base = Path(app_dir) if app_dir else Path(__file__).resolve().parents[2]
    n = max(len(originals), len(translations))
    sources = [str(originals[i] if i < len(originals) else "") for i in range(n)]
    raws = [str(translations[i] if i < len(translations) else "") for i in range(n)]

    # Claim MT ownership (already produced upstream)
    owners = get_owner_registry(task_id)
    for i in range(n):
        try:
            owners.claim("mt_raw", "MTEngine", segment_index=i)
        except Exception:
            pass

    # Prepare segments_data early — CATP needs slot timings during naturalize
    segments_data: list[dict] = []
    if info is not None:
        segments_data = list(info.get("segments_data") or [])
        while len(segments_data) < n:
            segments_data.append({"index": len(segments_data)})
    slot_ms_list = [
        _slot_ms_from_seg(segments_data[i] if i < len(segments_data) else None)
        for i in range(n)
    ]
    reserve_for_catp: list[int] | None = None
    tmp_reserve: list[int] = []
    have_reserve = False
    for i in range(n):
        seg = segments_data[i] if i < len(segments_data) else {}
        if isinstance(seg, dict) and seg.get("reserve_ms") is not None:
            try:
                tmp_reserve.append(max(0, int(seg.get("reserve_ms"))))
                have_reserve = True
                continue
            except (TypeError, ValueError):
                pass
        tmp_reserve.append(0)
    if have_reserve:
        reserve_for_catp = tmp_reserve

    polished, nat_metas = _rule_naturalize(
        raws,
        sources,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        task_id=task_id,
        app_dir=base,
        slot_ms_list=slot_ms_list,
        reserve_ms_list=reserve_for_catp,
    )

    metrics = TPSMetrics(task_id=task_id)
    results: list[TPSSegmentResult] = []
    out_texts: list[str] = []
    manual: list[int] = []

    for i in range(n):
        t0 = time.perf_counter()
        original = sources[i]
        raw_mt = raws[i] if i < len(raws) else ""
        text = polished[i] if i < len(polished) else raws[i]
        naturalized = text
        nat_meta = nat_metas[i] if i < len(nat_metas) else {}
        llm_calls = 0
        path = TPSPath.FAST
        status = TQEStatus.PASS
        reasons: list[str] = []
        retry_count = 0
        judge_used = False
        retry_text = ""
        judge_text = ""

        # Preempt healable phrase loops before QA / live unsafe wipe.
        try:
            from engines.mt.cross_script_guard import deflate_phrase_loop, has_phrase_loop

            if text and has_phrase_loop(text, min_repeats=2):
                deflated = deflate_phrase_loop(text)
                if deflated and not has_phrase_loop(deflated, min_repeats=2):
                    text = deflated
                    naturalized = deflated
        except Exception:
            pass

        try:
            from engines.mt.dirty_mt import compute_dirty_mt_score

            dirty = compute_dirty_mt_score(
                original, raw_mt, tgt_lang=tgt_lang
            ).to_dict()
        except Exception:
            dirty = {"dirty": False, "dirty_mt_score": 0.0, "reasons": []}

        qa = run_fast_qa(
            original,
            text,
            context={
                "target_lang": tgt_lang,
                "source_lang": src_lang,
                "index": i,
                "raw_mt": raw_mt,
                "naturalized": naturalized,
                "app_dir": str(base),
                "project_id": (info or {}).get("glossary_id")
                or (info or {}).get("project_id"),
            },
        )
        if qa.passed:
            path = TPSPath.FAST
            status = TQEStatus.PASS
            metrics.fast_path_count += 1
        else:
            reasons = list(qa.reason_codes)
            for c in reasons:
                metrics.add_reason(c)
            # Retry exactly once
            text, retry_llm = _retry_meaning_grammar(
                original,
                text,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                reason_codes=reasons,
            )
            retry_text = text
            retry_count = 1
            llm_calls += retry_llm
            try:
                if any(
                    c in reasons
                    for c in ("meaning_loss", "severe_truncation", "meaning_collapse")
                ):
                    owners.claim(
                        "semantic_rewrite", "SemanticRewriteOwner", segment_index=i
                    )
                if any(
                    c in reasons
                    for c in (
                        "incomplete",
                        "incomplete_sentence",
                        "orphan_clause_glue",
                        "orphan_clause_prefix",
                        "dirty_mt_noop",
                        "nonsense_calque",
                    )
                ):
                    owners.claim(
                        "grammar_rewrite", "GrammarRewriteOwner", segment_index=i
                    )
            except Exception as ow_exc:
                logger.debug("owner claim retry: %s", ow_exc)

            qa2 = run_fast_qa(
                original,
                text,
                context={
                    "target_lang": tgt_lang,
                    "source_lang": src_lang,
                    "index": i,
                    "raw_mt": raw_mt,
                    "naturalized": text,
                    "app_dir": str(base),
                },
            )
            if qa2.passed:
                path = TPSPath.RETRY
                status = TQEStatus.PASS
                # Drop stale fail codes from pre-retry QA — PASS must not carry
                # meaning_collapse while approving text (_tmp_3333).
                reasons = list(qa2.reason_codes)
                metrics.retry_path_count += 1
            else:
                reasons = list(qa2.reason_codes) or reasons
                for c in qa2.reason_codes:
                    metrics.add_reason(c)
                # LLM Judge once
                judged, ok, j_calls = _llm_judge(
                    original, text, errors=qa2.errors, tgt_lang=tgt_lang
                )
                llm_calls += j_calls
                judge_used = j_calls > 0
                text = judged
                judge_text = judged
                if ok:
                    path = TPSPath.LLM_JUDGE
                    status = TQEStatus.PASS
                    reasons = []
                    metrics.llm_judge_count += 1
                else:
                    # Re-check after judge candidate
                    qa3 = run_fast_qa(
                        original,
                        text,
                        context={
                            "target_lang": tgt_lang,
                            "source_lang": src_lang,
                            "index": i,
                            "raw_mt": raw_mt,
                            "naturalized": text,
                            "app_dir": str(base),
                        },
                    )
                    if qa3.passed:
                        path = TPSPath.LLM_JUDGE
                        status = TQEStatus.PASS
                        reasons = list(qa3.reason_codes)
                        metrics.llm_judge_count += 1
                    else:
                        path = TPSPath.MANUAL
                        status = TQEStatus.FAIL_MANUAL_REVIEW
                        metrics.manual_review_count += 1
                        manual.append(i)
                        reasons = list(qa3.reason_codes) or reasons

        elapsed = (time.perf_counter() - t0) * 1000
        metrics.segment_ms.append(elapsed)
        metrics.llm_calls.append(llm_calls)

        # Live gate: never PASS/approve if candidate still collapses or leaks source script.
        if status == TQEStatus.PASS:
            try:
                from engines.mt.cross_script_guard import (
                    meaning_collapse,
                    source_script_leak,
                )

                live_codes: list[str] = []
                if source_script_leak(
                    original, text, source_lang=src_lang, target_lang=tgt_lang
                ):
                    live_codes.append("source_script_leak")
                if meaning_collapse(
                    original, text, source_lang=src_lang, target_lang=tgt_lang
                ):
                    live_codes.append("meaning_collapse")
                    if (src_lang or "").lower().startswith(("zh", "ja", "ko")) or any(
                        "\u4e00" <= ch <= "\u9fff" for ch in (original or "")[:80]
                    ):
                        live_codes.append("cjk_meaning_collapse")
                if live_codes:
                    logger.warning(
                        "TPS demote PASS→MANUAL idx=%s live_unsafe=%s",
                        i,
                        live_codes,
                    )
                    status = TQEStatus.FAIL_MANUAL_REVIEW
                    path = TPSPath.MANUAL
                    reasons = sorted(set(reasons) | set(live_codes))
                    if i not in manual:
                        manual.append(i)
                    metrics.manual_review_count += 1
            except Exception as live_exc:
                logger.debug("TPS live unsafe gate skipped: %s", live_exc)

        if i < len(segments_data) and isinstance(segments_data[i], dict):
            try:
                from engines.trh import stamp_segment_recovery

                stamp_segment_recovery(
                    segments_data[i],
                    original=original,
                    raw_mt=raw_mt,
                    naturalized=naturalized,
                    retry_text=retry_text,
                    judge_text=judge_text,
                    approved=text if status == TQEStatus.PASS else "",
                    dirty=dirty,
                    naturalizer_meta=nat_meta,
                    tps_path=path.value,
                    tqe_status=status.value,
                    reason_codes=reasons,
                    retry_count=retry_count,
                    judge_used=judge_used,
                    dsal={
                        "applied": bool(segments_data[i].get("dsal_applied")),
                        "skip_reason": str(
                            segments_data[i].get("dsal_skip_reason") or ""
                        ),
                    },
                )
            except Exception as trh_exc:
                logger.debug("TRH stamp skipped: %s", trh_exc)

        if status == TQEStatus.PASS:
            if i < len(segments_data) and isinstance(segments_data[i], dict):
                approve_segment(
                    segments_data[i],
                    text,
                    tqe_status=status.value,
                    path=path.value,
                    task_id=task_id,
                    index=i,
                )
                # Preserve raw/naturalized after approve overwrites text fields
                segments_data[i]["raw_mt"] = raw_mt
                segments_data[i]["naturalized_text"] = naturalized
                if isinstance(nat_meta.get("catp"), dict):
                    segments_data[i]["catp"] = dict(nat_meta["catp"])
                    segments_data[i]["naturalizer_mode"] = nat_meta.get("naturalizer_mode") or ""
                    segments_data[i]["selected_variant"] = nat_meta.get("selected_variant") or ""
            out_texts.append(text)
        else:
            # Manual — keep diagnostic text but do NOT ship CJK hallucinations to TTS.
            # Healable phrase-loop echoes must not blank Final/TTS when deflate works.
            try:
                from engines.mt.cross_script_guard import (
                    deflate_phrase_loop,
                    has_phrase_loop,
                    meaning_collapse,
                    source_script_leak,
                )

                if text and (
                    "phrase_loop" in reasons
                    or has_phrase_loop(text, min_repeats=2)
                ):
                    deflated = deflate_phrase_loop(text)
                    if deflated and not has_phrase_loop(deflated, min_repeats=2):
                        qa_fix = run_fast_qa(
                            original,
                            deflated,
                            context={
                                "target_lang": tgt_lang,
                                "source_lang": src_lang,
                                "index": i,
                                "raw_mt": raw_mt,
                                "naturalized": deflated,
                                "app_dir": str(base),
                            },
                        )
                        if qa_fix.passed and not source_script_leak(
                            original,
                            deflated,
                            source_lang=src_lang,
                            target_lang=tgt_lang,
                        ):
                            text = deflated
                            naturalized = deflated
                            status = TQEStatus.PASS
                            path = TPSPath.RETRY
                            reasons = list(qa_fix.reason_codes)
                            if i < len(segments_data) and isinstance(
                                segments_data[i], dict
                            ):
                                approve_segment(
                                    segments_data[i],
                                    text,
                                    tqe_status=status.value,
                                    path=path.value,
                                    task_id=task_id,
                                    index=i,
                                )
                                segments_data[i]["raw_mt"] = raw_mt
                                segments_data[i]["naturalized_text"] = naturalized
                                segments_data[i]["tts_blocked"] = False
                                segments_data[i]["skip_tts"] = False
                                segments_data[i]["needs_manual_review"] = False
                            out_texts.append(text)
                            results.append(
                                TPSSegmentResult(
                                    index=i,
                                    status=status,
                                    path=path,
                                    text=text,
                                    original=original,
                                    reason_codes=reasons,
                                    llm_calls=llm_calls,
                                    elapsed_ms=elapsed,
                                    needs_manual_review=False,
                                )
                            )
                            continue
            except Exception as heal_exc:
                logger.debug("TPS phrase_loop heal-before-wipe skipped: %s", heal_exc)

            unsafe = set(reasons) & {
                "cjk_meaning_collapse",
                "meaning_collapse",
                "source_script_leak",
                "meaning_loss",
            }
            # phrase_loop-only collapse is voiceable after deflate; keep text.
            if unsafe == {"meaning_collapse"} and "phrase_loop" in set(reasons):
                try:
                    from engines.mt.cross_script_guard import (
                        deflate_phrase_loop,
                        has_phrase_loop,
                        meaning_collapse as _mc,
                    )

                    probe = deflate_phrase_loop(text) or text
                    hit = _mc(
                        original, probe, source_lang=src_lang, target_lang=tgt_lang
                    )
                    if probe and not has_phrase_loop(probe, min_repeats=2) and (
                        not hit or hit.get("reasons") == ["phrase_loop"]
                    ):
                        text = probe
                        naturalized = probe
                        unsafe = set()
                except Exception:
                    pass
            tts_safe = text
            if unsafe & {
                "cjk_meaning_collapse",
                "meaning_collapse",
                "source_script_leak",
            }:
                # Hallucination / wrong-script MT must not be voiced
                tts_safe = ""
            if i < len(segments_data) and isinstance(segments_data[i], dict):
                segments_data[i]["tqe_status"] = status.value
                segments_data[i]["tps_path"] = path.value
                segments_data[i]["approved_text"] = ""
                segments_data[i]["rejected_translation"] = text
                segments_data[i]["text"] = tts_safe
                segments_data[i]["final_text"] = tts_safe
                segments_data[i]["plain_text"] = tts_safe
                segments_data[i]["raw_mt"] = raw_mt
                segments_data[i]["naturalized_text"] = naturalized
                segments_data[i]["needs_manual_review"] = True
                segments_data[i]["tps_reason_codes"] = list(reasons)
                if tts_safe == "" and text:
                    segments_data[i]["tts_blocked"] = True
                    segments_data[i]["tts_blocked_reason"] = sorted(unsafe)[0] if unsafe else "manual_fail"
                    segments_data[i]["skip_tts"] = True
                if isinstance(nat_meta.get("catp"), dict):
                    segments_data[i]["catp"] = dict(nat_meta["catp"])
            out_texts.append(tts_safe)

        results.append(
            TPSSegmentResult(
                index=i,
                status=status,
                path=path,
                text=text,
                original=original,
                reason_codes=reasons,
                llm_calls=llm_calls,
                elapsed_ms=elapsed,
                needs_manual_review=status == TQEStatus.FAIL_MANUAL_REVIEW,
            )
        )

    metrics.dual_writer_violations = owners.dual_writer_violations
    if info is not None:
        info["segments_data"] = segments_data
        info["tps"] = True
        info["tps_manual_indices"] = list(manual)
        info["naturalizer_executed"] = True
        sync_audits_approved(info)
        # Mutation attempts counter
        mut = sum(
            int(s.get("approved_text_mutation_attempts") or 0)
            for s in segments_data
            if isinstance(s, dict)
        )
        metrics.approved_text_mutation_attempts = mut
        info["tps_metrics"] = metrics.to_dict()
        try:
            from engines.trh import write_segment_trace

            traces = list(info.get("trh_segment_traces") or [])
            path_tr = write_segment_trace(
                base,
                task_id,
                traces,
                session_dir=session_dir or info.get("session_dir"),
            )
            if path_tr:
                info["segment_trace_path"] = str(path_tr)
                info["trh_segment_trace_path"] = str(path_tr)
        except Exception as tr_exc:
            logger.debug("TRH trace write skipped: %s", tr_exc)

    if persist_metrics:
        try:
            write_tps_metrics(
                base,
                metrics,
                session_dir=session_dir or (info or {}).get("session_dir"),
            )
        except Exception as exc:
            logger.debug("tps metrics write skipped: %s", exc)

    gate_passed = len(manual) == 0 and n > 0
    return TPSBatchResult(
        task_id=task_id,
        segments=results,
        metrics=metrics,
        texts=out_texts,
        manual_indices=manual,
        gate_passed=gate_passed,
    )
