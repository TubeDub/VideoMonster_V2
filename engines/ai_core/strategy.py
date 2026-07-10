"""AI Core — Strategy.

AI Core does not translate; it *decides*. From a :class:`ProjectProfile` it
produces a :class:`ProjectStrategy`: the concrete decisions every downstream
executor (adaptation engine, LLM gateway, voice director, TTS, mixer) must obey
for this project — how many variants to generate, which speed/quality mode to
run, whether rewriting is needed, how many attempts, which quality checks to
enforce, and the default voice delivery.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Canonical speed/quality modes (mirror translation_adapt modes).
MODE_FAST = "fast"
MODE_BALANCED = "balanced"
MODE_MAX_QUALITY = "max_quality"


@dataclass
class ProjectStrategy:
    """The decisions AI Core makes for the whole project."""

    speed_mode: str = MODE_BALANCED
    per_segment_budget_s: float = 0.0     # 0 → resolved from mode
    project_budget_s: float = 0.0         # 0 → unlimited (soft telemetry)

    use_llm: bool = True
    # Decision Engine policy: off | problem_only | always (Task 3/9/10).
    llm_policy: str = "problem_only"
    min_variants: int = 5                 # ТЗ: at least 5
    max_variants: int = 10                # ТЗ: at most 10
    variants_per_round: int = 3
    min_rounds: int = 2
    max_rounds: int = 6                   # bounded — never an infinite loop
    rewrite_required: bool = True
    # Parallelism: 0 → AI Core auto-sizes from CPU/LLM (Task 5).
    max_parallel_segments: int = 0

    # Quality gates AI Core enforces (all mandatory per ТЗ, listed for the report).
    checks: list[str] = field(default_factory=lambda: [
        "meaning", "grammar", "naturalness", "emotion",
        "timing", "entity", "sentence_integrity", "slot_fit",
    ])
    predict_before_tts: bool = True
    protect_entities: bool = True
    block_hallucinations: bool = True
    forbid_truncation: bool = True

    # Voice delivery defaults (AI decides; user does not set these by hand).
    voice_emotion: str = "neutral"
    voice_tempo: str = "medium"

    # Music / ambience preservation (only original speech is replaced).
    preserve_music: bool = True
    ducking_enabled: bool = True

    model: str = ""
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def adaptation_profile_override(self) -> dict[str, Any]:
        """Profile dict consumed by ai_adaptation_engine for this run."""
        return {
            "min_rounds": self.min_rounds,
            "max_rounds": self.max_rounds,
            "variants_per_round": self.variants_per_round,
            "min_variants": self.min_variants,
            "max_variants": self.max_variants,
            "llm_policy": self.llm_policy,
        }


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def build_strategy(
    profile,
    *,
    requested_mode: str | None = None,
    llm_available: bool | None = None,
    model: str = "",
) -> ProjectStrategy:
    """Turn a :class:`ProjectProfile` into concrete project decisions.

    ``requested_mode`` (user's Fast/Balance/Max-Quality choice) is honoured;
    otherwise AI Core picks a mode from the project profile.
    """
    strat = ProjectStrategy(model=model)
    rationale = strat.rationale

    # ── Speed / quality mode ─────────────────────────────────────────────
    # IMPORTANT (P0 hang fix): do NOT auto-select max_quality. Defaulting to
    # max_quality made AI Core fire a full 10-variant LLM rewrite on EVERY
    # segment sequentially, which froze the Translation stage. The safe default
    # is BALANCED (intelligent adaptation only for problem segments).
    if requested_mode:
        strat.speed_mode = _normalize_mode(requested_mode)
        rationale.append(f"mode: user requested → {strat.speed_mode}")
    else:
        strat.speed_mode = MODE_BALANCED
        rationale.append("mode: default → balanced (LLM only for problem segments)")

    # ── LLM usage ────────────────────────────────────────────────────────
    if llm_available is None:
        try:
            from engines.ai_core import llm_gateway

            llm_available = llm_gateway.is_available()
        except Exception:
            llm_available = False
    strat.use_llm = bool(llm_available)

    # ── Decision policy + variant budget per quality profile ─────────────
    #   fast         → LLM off (rule-based only), fastest
    #   balanced     → LLM only for problem segments, small variant budget
    #   max_quality  → full LLM rewrite on every overflow segment, 5–10 variants
    if not llm_available:
        strat.llm_policy = "off"
        strat.rewrite_required = False
        strat.min_variants = 0
        strat.max_variants = 0
        strat.min_rounds = 0
        strat.max_rounds = 0
        rationale.append("llm: unavailable → rule-based prep only (no rewrite)")
    elif strat.speed_mode == MODE_FAST:
        strat.llm_policy = "off"
        strat.rewrite_required = False
        strat.min_variants = 0
        strat.max_variants = 0
        strat.min_rounds = 0
        strat.max_rounds = 0
        rationale.append("fast: rule-based only, minimal rewrite")
    elif strat.speed_mode == MODE_MAX_QUALITY:
        strat.llm_policy = "always"
        strat.rewrite_required = True
        base = 10 if getattr(profile, "complexity", "medium") == "high" else 8
        strat.min_variants = _clamp(base, 5, 10)
        strat.max_variants = 10
        strat.variants_per_round = 3
        strat.min_rounds = _clamp(-(-strat.min_variants // 3), 2, 6)
        strat.max_rounds = 6
        rationale.append("max_quality: full LLM rewrite on every overflow segment")
    else:  # balanced
        strat.llm_policy = "problem_only"
        strat.rewrite_required = True
        strat.min_variants = 5
        strat.max_variants = 8
        strat.variants_per_round = 3
        strat.min_rounds = 2
        strat.max_rounds = 4
        rationale.append("balanced: intelligent adaptation only for problem segments")

    if strat.use_llm and strat.max_variants:
        rationale.append(
            f"variants: {strat.min_variants}–{strat.max_variants} "
            f"({strat.variants_per_round}/round, up to {strat.max_rounds} rounds)"
        )

    # ── Voice delivery decisions ─────────────────────────────────────────
    strat.voice_emotion = getattr(profile, "dominant_emotion", "neutral")
    strat.voice_tempo = getattr(profile, "tempo", "medium")
    rationale.append(
        f"voice: emotion={strat.voice_emotion}, tempo={strat.voice_tempo}"
    )

    # ── Music preservation (always keep music/SFX; replace only speech) ──
    strat.preserve_music = True
    strat.ducking_enabled = getattr(profile, "content_type", "movie") not in {"audiobook"}
    rationale.append("music: preserve background; duck under speech")

    return strat


def _normalize_mode(value: str) -> str:
    try:
        from engines.translation_adapt import normalize_speed_mode

        return normalize_speed_mode(value)
    except Exception:
        v = str(value or "").strip().lower()
        if v in {"fast", "быстро"}:
            return MODE_FAST
        if v in {"max_quality", "quality", "максимальное качество", "max"}:
            return MODE_MAX_QUALITY
        return MODE_BALANCED
