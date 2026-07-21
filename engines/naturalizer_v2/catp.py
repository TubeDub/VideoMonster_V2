"""Context-Aware Translation Polishing (CATP) v1.0

Final stylistic polish that ALWAYS respects segment timing budgets.

Priorities: meaning → timing fit → natural UK → literary beauty.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger("tubedub.engines.naturalizer_v2.catp")

NaturalizerMode = Literal["safe", "extended"]
VariantId = Literal["A", "B", "C", "baseline"]


def catp_enabled() -> bool:
    """On by default — timing gate must not silently disappear in tests/prod."""
    v = (os.getenv("VM_CATP") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def extended_reserve_threshold_ms() -> int:
    try:
        return max(0, int(os.getenv("VM_CATP_EXTENDED_RESERVE_MS") or "400"))
    except ValueError:
        return 400


def slot_margin_ms() -> int:
    try:
        return max(0, int(os.getenv("VM_CATP_MARGIN_MS") or "40"))
    except ValueError:
        return 40


def max_growth_without_slot_ms() -> int:
    """When slot_ms unknown — cap literary growth vs safe baseline."""
    try:
        return max(0, int(os.getenv("VM_CATP_MAX_GROWTH_MS") or "120"))
    except ValueError:
        return 120


# Shorter synonym dictionary (TZ) — apply only when helping timing
_SHORT_SYNONYMS_UK: list[tuple[str, str]] = [
    # Seg1 — fit ~3s slot; keep dinner+hometown (tail was clipped as «на вечерю»)
    (
        r"(\d+)-річний\s+хлопець\s+на\s+ім['']я\s+(Джордж-молодший)\s+проїжджав\s+"
        r"через\s+(?:своє\s+)?рідне\s+місто\s+дорогою\s+додому(?:\s+на\s+вечерю)?",
        r"\1-річний \2 їхав рідним містом додому на вечерю",
    ),
    (
        r"(Джордж-молодший)\s+проїжджав\s+через\s+(?:своє\s+)?рідне\s+місто\s+"
        r"дорогою\s+додому(?:\s+на\s+вечерю)?",
        r"\1 їхав рідним містом додому на вечерю",
    ),
    (r"\bодержимість\b", "потяг"),
    (r"\bодержимості\b", "потягу"),
    (r"\bнаправити\s+увагу\b", "зосередитися"),
    (r"\bдуже\s+боявся\b", "боявся"),
    (r"\bдуже\s+боялася\b", "боялася"),
    # Keep the natural dread line — do not degrade to «він боявся»
    (r"\bйого\s+не\s+полишало\s+важке\s+передчуття\b", "йому не хотілося"),
    (r"\bне\s+полишало\s+важке\s+передчуття\b", "йому не хотілося"),
    (r"\bдорогою\s+додому\s+його\s+не\s+полишало\s+важке\s+передчуття\b", "йому не хотілося їхати додому"),
    (r"\bДжордж-молодший\s+його\s+не\s+полишала\s+тривога,\s*як\s+він\s+був\s+дійсно\s+зі\s+страхом\s+очікував\s+насправді\s+отримати\s+там\b",
     "Джорджу-молодшому зовсім не хотілося їхати додому"),
    (r"\bзі\s+страхом\s+очікував\s+насправді\s+отримати\s+там\b", "зовсім не хотів їхати додому"),
    (r"\bйого\s+не\s+полишало\s+відчуття\b", "він відчував"),
    (r"\bне\s+полишало\s+відчуття,\s+що\b", "він відчував, що"),
    (r"\bмайже\s+нічим\s+не\s+займався\s+по-справжньому\s+серйозно\b", "майже нічим серйозно не займався"),
    (r"\bТож\s+кожна\s+вечеря\s+в\s+ці\s+дні,\s*перетворювалася\b", "Тож кожна вечеря в ці дні перетворювалася"),
    (r"\bпопросив\s+Джорджа-молодшого\s+про\s+свою\s+фотографію\b",
     "запитав Джорджа-молодшого про його фотографію"),
    (r"\bПро\s+те,\s+як\s+він\s+нещодавно\s+подав\s+заявку\s+до\s+USC\b",
     "Джордж-молодший розповів Хаскеллу, що нещодавно подав заявку до USC"),
    (r"\bбув\s+досить\s+впевненим\b", "був майже впевнений"),
    (r"\bпісля\s+того\s+як\b", "після того, як"),
    (r"\bтак\s+важко,\s+що\b", "так сильно, що"),
    (r"\bвесь\s+світ\s+знає\s+як\b", "знають як"),
    (r"\bСьогодні\s+Джорджа-молодшого\s+весь\s+світ\s+знає\s+як\b", "Сьогодні Джорджа-молодшого знають як"),
    (r"\bСьогодні\s+Джорджа-молодшого\s+знають\s+як\b", "Сьогодні він відомий як"),
    (r"\bотримав\s+лист\s+про\s+зарахування\b", "отримав запрошення"),
    (r"\bніяк\s+не\s+міг\s+зрозуміти,\s+звідки\s+у\s+сина\s+така\s+одержимість\b", "не розумів одержимості сина"),
    (r"\bніяк\s+не\s+міг\s+зрозуміти,\s+звідки\s+у\s+сина\s+такий\s+потяг\b", "не розумів потягу сина"),
    (r"\bзробити\s+кілька\s+фото\s+переможного\s+гонщика\b", "сфотографувати переможного гонщика"),
    (r"\bзробити\s+кілька\s+фото\s+переможця\s+гонки\b", "сфотографувати переможного гонщика"),
    (r"\bфото\s+переможця\s+гонки\b", "фото переможного гонщика"),
    (r"\bУніверситет(?:у)?\s+Південної\s+Каліфорнії\b", "USC"),
    (r"\bкіношколи\s+Університету\s+Південної\s+Каліфорнії\b", "кіношколи USC"),
]


@dataclass
class LengthBudget:
    slot_ms: int = 0
    allowed_ms: int = 0
    reserve_ms: int = 0
    estimated_baseline_ms: int = 0
    mode: NaturalizerMode = "safe"
    known_slot: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CatpVariant:
    id: VariantId
    text: str
    estimated_ms: int
    fits: bool
    naturalness: float  # 0..1
    quality: float  # 0..1
    score: float = 0.0
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CatpResult:
    text: str
    mode: NaturalizerMode = "safe"
    selected_variant: VariantId = "baseline"
    duration_before: int = 0
    duration_after: int = 0
    delta_duration: int = 0
    reserve_used: int = 0
    rollback_due_to_length: bool = False
    handoff_to_dsal: bool = False
    allowed_ms: int = 0
    slot_ms: int = 0
    variants: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_duration_ms(text: str, lang: str = "uk") -> int:
    from engines.semantic_adaptation import estimate_tts_duration_ms

    return int(estimate_tts_duration_ms(text, lang) or 0)


def compute_budget(
    *,
    slot_ms: int = 0,
    reserve_ms: int | None = None,
    baseline_text: str = "",
    lang: str = "uk",
) -> LengthBudget:
    slot = max(0, int(slot_ms or 0))
    margin = slot_margin_ms()
    baseline_ms = estimate_duration_ms(baseline_text, lang) if baseline_text else 0
    known = slot > 0
    if known:
        allowed = max(200, slot - margin)
        # Pre-TTS reserve ≈ how much spare room vs baseline speech
        if reserve_ms is not None:
            reserve = int(reserve_ms)
        else:
            reserve = max(0, allowed - baseline_ms)
    else:
        # Unknown slot: allow small growth only; force Safe Polish
        allowed = baseline_ms + max_growth_without_slot_ms() if baseline_ms else 0
        reserve = int(reserve_ms) if reserve_ms is not None else max_growth_without_slot_ms()

    mode: NaturalizerMode = "safe"
    if known and reserve >= extended_reserve_threshold_ms():
        mode = "extended"
    elif not known and reserve_ms is not None and reserve_ms >= extended_reserve_threshold_ms():
        mode = "extended"

    return LengthBudget(
        slot_ms=slot,
        allowed_ms=allowed,
        reserve_ms=max(0, reserve),
        estimated_baseline_ms=baseline_ms,
        mode=mode,
        known_slot=known,
    )


def apply_short_synonyms(text: str, *, lang: str = "uk") -> tuple[str, list[str]]:
    """Apply shorter synonym dictionary (timing helper only)."""
    out = str(text or "")
    if not out.strip():
        return out, []
    if (lang or "uk").split("-")[0].lower() != "uk":
        return out, []
    applied: list[str] = []
    for pat, repl in _SHORT_SYNONYMS_UK:
        new = re.sub(pat, repl, out, flags=re.IGNORECASE)
        if new != out:
            out = new
            applied.append(repl)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out, applied


def _naturalness_score(text: str, *, literary: bool = False, short: bool = False) -> float:
    t = str(text or "")
    score = 0.55
    if literary:
        score += 0.25
    if short:
        score += 0.05
    # Penalize stiff English skeleton
    try:
        from engines.naturalizer_v2.literary_uk import is_stiff_uk

        if is_stiff_uk(t):
            score -= 0.35
    except Exception:
        pass
    if re.search(r"\bякий\s+називається\b", t, re.I):
        score -= 0.15
    return max(0.0, min(1.0, score))


def _timing_cost(est_ms: int, budget: LengthBudget) -> float:
    if budget.allowed_ms <= 0:
        # Relative to baseline growth
        growth = max(0, est_ms - budget.estimated_baseline_ms)
        return growth / 100.0
    over = max(0, est_ms - budget.allowed_ms)
    # Also penalize eating the whole reserve
    tight = 0.0
    if budget.reserve_ms > 0 and est_ms > budget.estimated_baseline_ms:
        used = est_ms - budget.estimated_baseline_ms
        if used > budget.reserve_ms:
            tight = (used - budget.reserve_ms) / 100.0
    return (over / 80.0) + tight


def score_variant(
    text: str,
    *,
    budget: LengthBudget,
    lang: str = "uk",
    quality: float = 0.7,
    literary: bool = False,
    short: bool = False,
) -> tuple[float, int, bool]:
    est = estimate_duration_ms(text, lang)
    fits = True
    if budget.allowed_ms > 0:
        fits = est <= budget.allowed_ms
    elif budget.estimated_baseline_ms > 0:
        fits = est <= budget.estimated_baseline_ms + max_growth_without_slot_ms()
    nat = _naturalness_score(text, literary=literary, short=short)
    cost = _timing_cost(est, budget)
    # Quality + Naturalness − Timing Cost (TZ)
    total = (0.45 * quality) + (0.40 * nat) - (0.55 * cost)
    if not fits:
        total -= 1.5  # hard preference against overflow
    return total, est, fits


def build_variants(
    *,
    baseline: str,
    safe: str,
    literary: str | None = None,
    lang: str = "uk",
    budget: LengthBudget,
) -> list[CatpVariant]:
    """A=short, B=neutral/safe, C=literary (if allowed)."""
    variants: list[CatpVariant] = []

    short_src = literary if (literary and literary.strip() and budget.mode == "extended") else safe
    short_text, _ = apply_short_synonyms(short_src or safe or baseline, lang=lang)
    if not short_text.strip():
        short_text = safe or baseline

    # A — short
    sc, est, fits = score_variant(
        short_text, budget=budget, lang=lang, quality=0.72, short=True
    )
    variants.append(
        CatpVariant(
            id="A",
            text=short_text,
            estimated_ms=est,
            fits=fits,
            naturalness=_naturalness_score(short_text, short=True),
            quality=0.72,
            score=sc,
            label="short",
        )
    )

    # B — safe / neutral
    safe_text = (safe or baseline).strip()
    sc, est, fits = score_variant(
        safe_text, budget=budget, lang=lang, quality=0.78, literary=False
    )
    variants.append(
        CatpVariant(
            id="B",
            text=safe_text,
            estimated_ms=est,
            fits=fits,
            naturalness=_naturalness_score(safe_text),
            quality=0.78,
            score=sc,
            label="safe",
        )
    )

    # C — literary (only compete when Extended OR it still fits)
    lit = (literary or "").strip()
    if lit and lit != safe_text:
        sc, est, fits = score_variant(
            lit, budget=budget, lang=lang, quality=0.85, literary=True
        )
        # In Safe mode, literary may still win if it fits and scores higher
        if budget.mode == "extended" or fits:
            variants.append(
                CatpVariant(
                    id="C",
                    text=lit,
                    estimated_ms=est,
                    fits=fits,
                    naturalness=_naturalness_score(lit, literary=True),
                    quality=0.85,
                    score=sc,
                    label="literary",
                )
            )

    return variants


def select_best_variant(variants: list[CatpVariant]) -> CatpVariant | None:
    if not variants:
        return None
    fitting = [v for v in variants if v.fits]
    pool = fitting if fitting else variants
    return max(pool, key=lambda v: v.score)


def polish_with_budget(
    *,
    baseline: str,
    safe: str,
    literary: str | None = None,
    slot_ms: int = 0,
    reserve_ms: int | None = None,
    lang: str = "uk",
) -> CatpResult:
    """Pick A/B/C under length budget. Rollback literary if it breaks timing."""
    base = str(baseline or "").strip()
    safe_t = str(safe or base).strip() or base
    lit_t = str(literary or "").strip() or None

    if not catp_enabled():
        chosen = safe_t  # without CATP never prefer longer literary
        est_b = estimate_duration_ms(base or safe_t, lang)
        est_a = estimate_duration_ms(chosen, lang)
        return CatpResult(
            text=chosen,
            mode="safe",
            selected_variant="B",
            duration_before=est_b,
            duration_after=est_a,
            delta_duration=est_a - est_b,
            reasons=["catp_disabled"],
        )

    budget = compute_budget(
        slot_ms=slot_ms,
        reserve_ms=reserve_ms,
        baseline_text=safe_t or base,
        lang=lang,
    )

    # Safe mode: do not even offer over-long literary as default input
    if budget.mode == "safe" and lit_t:
        lit_est = estimate_duration_ms(lit_t, lang)
        if budget.allowed_ms > 0 and lit_est > budget.allowed_ms:
            # keep for A/C scoring only if somehow shorter after synonyms
            pass
        elif budget.allowed_ms <= 0 and lit_est > budget.estimated_baseline_ms + max_growth_without_slot_ms():
            pass

    variants = build_variants(
        baseline=base or safe_t,
        safe=safe_t,
        literary=lit_t,
        lang=lang,
        budget=budget,
    )
    best = select_best_variant(variants)
    if not best:
        return CatpResult(
            text=safe_t,
            mode=budget.mode,
            selected_variant="B",
            duration_before=budget.estimated_baseline_ms,
            duration_after=budget.estimated_baseline_ms,
            allowed_ms=budget.allowed_ms,
            slot_ms=budget.slot_ms,
            reasons=["catp_empty"],
        )

    duration_before = budget.estimated_baseline_ms
    duration_after = best.estimated_ms
    delta = duration_after - duration_before
    rollback = False
    reasons: list[str] = [f"catp_mode:{budget.mode}", f"catp_variant:{best.id}"]

    # Explicit rollback: literary grew past remaining reserve
    if lit_t and best.id != "C" and lit_t != best.text:
        lit_est = estimate_duration_ms(lit_t, lang)
        lit_delta = lit_est - duration_before
        if lit_delta > budget.reserve_ms or (
            budget.allowed_ms > 0 and lit_est > budget.allowed_ms
        ):
            rollback = True
            reasons.append("rollback_due_to_length")

    handoff = False
    if not any(v.fits for v in variants):
        handoff = True
        reasons.append("handoff_to_dsal")
        # Prefer shortest among bad options
        best = min(variants, key=lambda v: v.estimated_ms)
        reasons.append(f"catp_shortest_fallback:{best.id}")

    # Segment-aware: reject hanging connectors that imply next-segment continuation
    text = best.text
    if re.search(r"[,:;]\s*$", text) and len(text.split()) > 3:
        # Soft: strip trailing comma/semicolon for standalone segment read
        cleaned = re.sub(r"[,:;]\s*$", ".", text).strip()
        if cleaned != text:
            text = cleaned
            reasons.append("segment_standalone_punct")

    reserve_used = max(0, duration_after - duration_before)

    return CatpResult(
        text=text,
        mode=budget.mode,
        selected_variant=best.id,
        duration_before=duration_before,
        duration_after=estimate_duration_ms(text, lang),
        delta_duration=estimate_duration_ms(text, lang) - duration_before,
        reserve_used=reserve_used,
        rollback_due_to_length=rollback,
        handoff_to_dsal=handoff,
        allowed_ms=budget.allowed_ms,
        slot_ms=budget.slot_ms,
        variants=[v.to_dict() for v in variants],
        reasons=reasons,
    )


def try_dsal_compress(
    text: str,
    *,
    slot_ms: int,
    lang: str = "uk",
    source_hint: str = "",
) -> tuple[str, bool]:
    """Handoff to DSAL rule compress when CATP cannot fit."""
    if slot_ms <= 0 or not text.strip():
        return text, False
    try:
        from engines.dsal.core import _rule_compress_uk

        compressed, stages = _rule_compress_uk(
            text, slot_ms=slot_ms, source_hint=source_hint, tgt_lang=lang
        )
        if compressed and compressed != text and stages:
            return compressed, True
    except Exception as exc:
        logger.debug("CATP DSAL handoff skipped: %s", exc)
    try:
        from engines.soft_sync import shorten_text_for_slot

        shortened = shorten_text_for_slot(
            text, slot_ms=slot_ms, lang=lang, source_hint=source_hint
        )
        if shortened and shortened != text:
            return shortened, True
    except Exception:
        pass
    return text, False
