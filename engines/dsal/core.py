"""Duration-Semantic Adaptation Layer (DSAL) — TZ v4.0.

Runs BEFORE TRANSLATION LOCK. Rule-based first; LLM is optional enhancement.
Never a hard dependency on LLM.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from engines.dsal.clause_coverage import (
    ClauseCoverageResult,
    compute_clause_coverage,
    restore_missing_clauses,
)
from engines.semantic_adaptation import estimate_tts_duration_ms

# Target band ±10% (max ±15% per TZ)
BAND_GREEN = 0.10
BAND_YELLOW = 0.25
CLAUSE_COVERAGE_MIN = 0.85


@dataclass
class DurationAnalysis:
    slot_ms: int
    predicted_tts_ms: int
    actual_tts_ms: int
    delta_ms: int  # slot - speech (positive = underflow / empty time)
    delta_pct: float
    band: str  # green | yellow | red
    expand_required: bool
    compress_required: bool
    duration_match_score: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DSALResult:
    text: str
    changed: bool
    analysis: DurationAnalysis
    stages: list[str] = field(default_factory=list)
    adaptation_executed: bool = False
    method: str = "none"
    detail: str = ""
    clause_coverage: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "changed": self.changed,
            "analysis": self.analysis.to_dict(),
            "stages": list(self.stages),
            "adaptation_executed": self.adaptation_executed,
            "method": self.method,
            "detail": self.detail,
            "clause_coverage": self.clause_coverage,
        }


# Timing-only fillers — never add to spoken text (audio stage handles underflow).
_UK_ELABORATIONS = (
    " саме в цей момент",
    " насправді",
    " у той самий час",
    " як з'ясувалося згодом",
    " і це було важливо",
    " саме тоді",
    " прямо біля дому",
    " без жодних попереджень",
    " раптово і несподівано",
    " у ту саму мить",
    " і життя змінилося назавжди",
)

_UK_ELABORATION_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(e.strip()) for e in _UK_ELABORATIONS if e.strip())
    + r")",
    re.I,
)


_UK_SYNONYM_EXPAND: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bпотім\b", re.I), "після цього"),
    (re.compile(r"\bколи\b", re.I), "у той момент, коли"),
    (re.compile(r"\bдуже\b", re.I), "надзвичайно"),
    (re.compile(r"\bтам\b", re.I), "у тому місці"),
    (re.compile(r"\bтут\b", re.I), "у цьому місці"),
]

# Reverse of expand + common UK filler/verbose → shorter (aggressive overflow).
_UK_SYNONYM_COMPRESS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bу той момент, коли\b", re.I), "коли"),
    (re.compile(r"\bпісля цього\b", re.I), "потім"),
    (re.compile(r"\bнадзвичайно\b", re.I), "дуже"),
    (re.compile(r"\bу тому місці\b", re.I), "там"),
    (re.compile(r"\bу цьому місці\b", re.I), "тут"),
    # Mid-sentence DSAL filler only — keep sentence-initial «Насправді» (In fact).
    (re.compile(r"(?<=[а-яіїєґ])\s+насправді\b", re.I), ""),
    (re.compile(r"\bсаме тоді\b", re.I), ""),
    (re.compile(r"\bсаме в цей момент\b", re.I), ""),
    (re.compile(r"\bу ту саму мить\b", re.I), ""),
    (re.compile(r"\bу той самий час\b", re.I), ""),
    (re.compile(r"\bпрямо біля дому\b", re.I), "біля дому"),
    (re.compile(r"\bбез жодних попереджень\b", re.I), ""),
    (re.compile(r"\bраптово і несподівано\b", re.I), "раптово"),
    (re.compile(r"\bяк з'ясувалося згодом\b", re.I), ""),
    (re.compile(r"\bі це було важливо\b", re.I), ""),
    (re.compile(r"\bі життя змінилося назавжди\b", re.I), ""),
    (re.compile(r"\bвін почав\b", re.I), "почав"),
    (re.compile(r"\bвін був\b", re.I), "був"),
    (re.compile(r"\bі потім\b", re.I), "потім"),
    (re.compile(r"\bі він\b", re.I), "і"),
    (
        re.compile(
            r"\bлежав\s+на\s+лікарняному\s+ліжку\s+у\s+відділенні\s+інтенсивної\s+терапії\s+"
            r"в\s+місцевій\s+лікарні\b",
            re.I,
        ),
        "лежав у реанімації місцевої лікарні",
    ),
    (re.compile(r"\bТак\s+два\s+тижні\s+раніше\b", re.I), "Два тижні раніше"),
    (re.compile(r"\s{2,}", re.I), " "),
]


def analyze_duration(
    *,
    slot_ms: int,
    text: str = "",
    tgt_lang: str = "uk",
    actual_tts_ms: int | None = None,
) -> DurationAnalysis:
    slot = max(0, int(slot_ms or 0))
    predicted = estimate_tts_duration_ms(text, tgt_lang) if text else 0
    actual = int(actual_tts_ms) if actual_tts_ms and actual_tts_ms > 0 else predicted
    speech = actual if actual > 0 else predicted
    delta = slot - speech if slot > 0 and speech > 0 else 0
    pct = (delta / slot) if slot > 0 else 0.0
    abs_pct = abs(pct)
    if slot <= 0 or speech <= 0:
        band = "red"
        score = 0
    elif abs_pct <= BAND_GREEN:
        band = "green"
        score = 100
    elif abs_pct <= BAND_YELLOW:
        band = "yellow"
        score = max(0, 100 - int(abs_pct * 400))
    else:
        band = "red"
        score = max(0, 100 - int(abs_pct * 400))
    expand = bool(slot > 0 and speech > 0 and delta > int(slot * BAND_GREEN))
    compress = bool(slot > 0 and speech > 0 and (-delta) > int(slot * BAND_GREEN))
    return DurationAnalysis(
        slot_ms=slot,
        predicted_tts_ms=predicted,
        actual_tts_ms=actual,
        delta_ms=delta,
        delta_pct=round(pct * 100.0, 2),
        band=band,
        expand_required=expand,
        compress_required=compress,
        duration_match_score=int(score),
    )


def strip_dsal_elaboration_fillers(text: str, *, tgt_lang: str = "uk") -> str:
    """Remove timing-only DSAL fillers that harm meaning when left in text.

    UK-only: never strip/restore «Насправді» on Russian (or other) targets.
    """
    out = str(text or "")
    if not out.strip():
        return out
    lang = str(tgt_lang or "uk").split("-")[0].lower()
    if lang != "uk":
        return out.strip()
    # Protect discourse-initial Насправді from filler wipe.
    _NAS = "\u0000NASPRAVDI\u0000"
    out = re.sub(r"(?i)^(\s*)насправді\b", r"\1" + _NAS, out, count=1)
    for elab in _UK_ELABORATIONS:
        token = elab.strip()
        if not token:
            continue
        if token.lower() == "насправді":
            # Mid-sentence only
            out = re.sub(r"(?<=[а-яіїєґА-ЯІЇЄҐ])\s+насправді\b", "", out, flags=re.I)
            continue
        out = re.sub(re.escape(token), "", out, flags=re.I)
    out = out.replace(_NAS, "Насправді")
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    # Keep a leading comma — pre_lock polish restores «Насправді» from EN «In fact».
    return out.strip()


def _rule_expand_uk(text: str, *, need_ms: int, tgt_lang: str) -> tuple[str, list[str]]:
    """Lengthen via meaning-preserving synonym swaps only — no filler padding.

    UK synonym table only — never rewrite Russian/other with UK forms.
    """
    stages: list[str] = []
    current = " ".join(str(text or "").split())
    if not current or need_ms <= 0:
        return current, stages
    if str(tgt_lang or "").split("-")[0].lower() != "uk":
        return current, stages
    base_ms = estimate_tts_duration_ms(current, tgt_lang)
    target_ms = base_ms + need_ms

    for _ in range(len(_UK_SYNONYM_EXPAND) + 2):
        if estimate_tts_duration_ms(current, tgt_lang) >= target_ms:
            break
        progressed = False
        for pat, repl in _UK_SYNONYM_EXPAND:
            if estimate_tts_duration_ms(current, tgt_lang) >= target_ms:
                break
            nxt = pat.sub(repl, current, count=1)
            if nxt != current:
                current = nxt
                stages.append(f"synonym_expand:{repl}")
                progressed = True
        if not progressed:
            break

    return " ".join(current.split()), stages


def _rule_compress_uk(
    text: str, *, slot_ms: int, source_hint: str, tgt_lang: str
) -> tuple[str, list[str]]:
    """Compress toward slot. Keeps partial wins; never all-or-nothing discard.

    UK synonym/elaboration tables only — skip for non-UK targets.
    """
    stages: list[str] = []
    current = " ".join(str(text or "").split())
    if not current or slot_ms <= 0:
        return current, stages
    if str(tgt_lang or "").split("-")[0].lower() != "uk":
        return current, stages

    target_ms = int(slot_ms * (1.0 + BAND_GREEN))

    # 1) Strip DSAL elaborations (safe — we may have added them on expand).
    for elab in _UK_ELABORATIONS:
        if elab in current:
            current = current.replace(elab, "")
            stages.append(f"strip_elab:{elab.strip()}")
    current = " ".join(current.split())

    # 2) Aggressive UK synonym / filler compress while over target.
    for _ in range(8):
        if estimate_tts_duration_ms(current, tgt_lang) <= target_ms:
            break
        progressed = False
        for pat, repl in _UK_SYNONYM_COMPRESS:
            if estimate_tts_duration_ms(current, tgt_lang) <= target_ms:
                break
            nxt = " ".join(pat.sub(repl, current, count=1).split())
            if nxt and nxt != current:
                current = nxt
                stages.append(f"synonym_compress:{repl or 'drop'}")
                progressed = True
        if not progressed:
            break

    # 3) Semantic rule stages (now keep partial).
    from engines.semantic_optimizer import optimize_rule_based_only

    result = optimize_rule_based_only(
        current,
        source_hint=source_hint,
        slot_ms=slot_ms,
        tgt_lang=tgt_lang,
    )
    if result.changed and result.text:
        current = " ".join(str(result.text).split())
        stages.extend(s.stage for s in result.stages if s.applied)

    return current, stages


def adapt_duration_semantic(
    text: str,
    *,
    source_hint: str = "",
    slot_ms: int,
    tgt_lang: str = "uk",
    actual_tts_ms: int | None = None,
    allow_llm: bool = False,
) -> DSALResult:
    """Rule-based DSAL (+ optional LLM polish when allow_llm=True)."""
    original = " ".join(str(text or "").split())
    analysis = analyze_duration(
        slot_ms=slot_ms,
        text=original,
        tgt_lang=tgt_lang,
        actual_tts_ms=actual_tts_ms,
    )

    stages: list[str] = []
    current = original
    method = "none"

    # P1: clause restore whenever coverage below threshold (even on green)
    cov: ClauseCoverageResult = compute_clause_coverage(
        source_hint, current, tgt_lang=tgt_lang
    )
    if cov.coverage < CLAUSE_COVERAGE_MIN or cov.missing:
        current, cov = restore_missing_clauses(
            current, source_hint, tgt_lang=tgt_lang
        )
        if cov.restored_phrases:
            stages.extend(f"clause_restore:{p}" for p in cov.restored_phrases)
            method = "clause_restore"
            analysis = analyze_duration(
                slot_ms=slot_ms,
                text=current,
                tgt_lang=tgt_lang,
                actual_tts_ms=None,
            )

    if slot_ms <= 0:
        return DSALResult(
            text=current,
            changed=current != original,
            analysis=analysis,
            stages=stages or ["no_slot"],
            adaptation_executed=current != original,
            method=method if current != original else "none",
            detail="no_slot",
            clause_coverage=cov.coverage,
        )

    if analysis.band == "green" and not stages:
        return DSALResult(
            text=current,
            changed=current != original,
            analysis=analysis,
            stages=["skip_green"] if current == original else stages,
            adaptation_executed=current != original,
            method=method if current != original else "none",
            detail="within ±10%",
            clause_coverage=cov.coverage,
        )

    if analysis.expand_required:
        need = max(0, analysis.delta_ms - int(slot_ms * BAND_GREEN))
        current, exp_stages = _rule_expand_uk(current, need_ms=need, tgt_lang=tgt_lang)
        stages.extend(exp_stages)
        method = "rule_expand" if exp_stages else (method or "rule_expand")
    elif analysis.compress_required:
        for _pass in range(3):
            prev = current
            current, cmp_stages = _rule_compress_uk(
                current, slot_ms=slot_ms, source_hint=source_hint, tgt_lang=tgt_lang
            )
            stages.extend(cmp_stages)
            method = "rule_compress"
            if current == prev:
                break
            mid = analyze_duration(
                slot_ms=slot_ms, text=current, tgt_lang=tgt_lang, actual_tts_ms=None
            )
            if not mid.compress_required:
                break
        current, cov2 = restore_missing_clauses(
            current, source_hint, tgt_lang=tgt_lang
        )
        if cov2.restored_phrases:
            stages.extend(f"clause_reapply:{p}" for p in cov2.restored_phrases)
            cov = cov2

    final_cov = compute_clause_coverage(
        source_hint, current, tgt_lang=tgt_lang
    )
    if final_cov.coverage < CLAUSE_COVERAGE_MIN or final_cov.missing:
        current, final_cov = restore_missing_clauses(
            current, source_hint, tgt_lang=tgt_lang
        )
        if final_cov.restored_phrases:
            stages.extend(f"clause_final:{p}" for p in final_cov.restored_phrases)
            method = method if method != "none" else "clause_restore"

    current = strip_dsal_elaboration_fillers(current, tgt_lang=tgt_lang)
    try:
        from engines.dsal.clause_coverage import strip_cross_lang_clause_orphans

        current = strip_cross_lang_clause_orphans(current)
    except Exception:
        pass
    changed = current != original
    final = analyze_duration(
        slot_ms=slot_ms,
        text=current,
        tgt_lang=tgt_lang,
        actual_tts_ms=None if changed else actual_tts_ms,
    )
    result = DSALResult(
        text=current,
        changed=changed,
        analysis=final,
        stages=stages or ["no_change"],
        adaptation_executed=changed,
        method=method if changed else "none",
        detail=(
            f"band={analysis.band}->{final.band} "
            f"delta={analysis.delta_ms}->{final.delta_ms} "
            f"clause={final_cov.coverage}"
        ),
        clause_coverage=final_cov.coverage,
    )

    # P3: optional LLM enhancement after rules (soft — never hard-fail)
    if allow_llm and final.band in ("yellow", "red"):
        try:
            from engines.dsal.llm_enhance import llm_enhance_duration

            result = llm_enhance_duration(
                result,
                source_hint=source_hint,
                tgt_lang=tgt_lang,
                slot_ms=slot_ms,
            )
        except Exception:
            pass
    return result


def stamp_dsal_on_segment(seg: dict[str, Any], result: DSALResult) -> None:
    """Write DSAL fields onto a segment (pre-LOCK)."""
    a = result.analysis
    seg["expand_required"] = bool(a.expand_required)
    seg["compress_required"] = bool(a.compress_required)
    seg["duration_match_score"] = int(a.duration_match_score)
    seg["dsal_band"] = a.band
    seg["dsal_delta_ms"] = int(a.delta_ms)
    seg["dsal_applied"] = bool(result.adaptation_executed)
    # HF5: always expose why DSAL did / did not rewrite text
    if result.adaptation_executed:
        seg["dsal_skip_reason"] = ""
    else:
        seg["dsal_skip_reason"] = str(
            result.detail
            or result.method
            or ("duration_only" if result.method == "duration_only" else "not_executed")
        )
    seg["clause_coverage"] = float(result.clause_coverage)
    seg["adaptation_executed"] = bool(
        result.adaptation_executed or seg.get("adaptation_executed")
    )
    trace = seg.setdefault("text_adaptation_trace", {})
    trace["expand_required"] = bool(a.expand_required)
    trace["duration_match_score"] = int(a.duration_match_score)
    trace["speech_difference_ms"] = int(a.delta_ms)
    trace["dsal_band"] = a.band
    trace["clause_coverage"] = float(result.clause_coverage)
    trace["dsal_skip_reason"] = seg.get("dsal_skip_reason") or ""
    if result.adaptation_executed:
        trace["executed"] = True
        trace["stages"] = list(trace.get("stages") or []) + [
            f"dsal:{s}" for s in result.stages
        ]
        if not trace.get("text_before"):
            trace["text_before"] = ""
        trace["text_after"] = result.text[:500]
        trace["expansion_strategy"] = result.method
    seg["dsal"] = result.to_dict()
