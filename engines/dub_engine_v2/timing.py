"""P405 AudioTimingOptimizer order + P406/P407 Overflow/Underflow (no text mutation)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.dub_engine_v2.models import AudioPlan, SpeechUnitV2
from engines.pipeline_integrity.exceptions import ArchitectureViolation

# P405 — fixed order (must not be violated)
ATO_ORDER: tuple[str, ...] = (
    "trim_silence",
    "pause_optimization",
    "prosody",
    "tempo",
    "micro_stretch",
    "borrow_time",
    "sentence_merge",
    "manual_review",
)

# Map Decision Policy step names → ATO canonical
_STEP_ALIASES = {
    "stretch": "micro_stretch",
    "prosody_optimization": "prosody",
}


@dataclass
class TimingAdjustment:
    speech_uuid: str
    steps_applied: list[str] = field(default_factory=list)
    tempo: float = 1.0
    stretch: float = 1.0
    trim_ms: int = 0
    pause_ms: int = 0
    breath_ms: int = 0
    borrow_ms: int = 0
    expected_duration_ms: int = 0
    fits: bool = True
    overflow: bool = False
    underflow: bool = False
    needs_decision: bool = False
    strategies_for_decision: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_steps(steps: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for s in steps:
        if s in ("ready",):
            continue
        name = _STEP_ALIASES.get(s, s)
        if name not in ATO_ORDER:
            continue
        if name not in out:
            out.append(name)
    # Enforce ATO order
    return [s for s in ATO_ORDER if s in out]


def assert_ato_order(steps: list[str]) -> None:
    idxs = [ATO_ORDER.index(s) for s in steps if s in ATO_ORDER]
    if idxs != sorted(idxs):
        raise ArchitectureViolation(
            f"P405 ATO order violated: {steps}",
            stage="dub_engine_v2",
            rule="ato_order",
        )


def optimize_timing(
    speech: SpeechUnitV2,
    plan: AudioPlan,
    *,
    strategy_steps: list[str] | None = None,
) -> TimingAdjustment:
    """
    Apply Decision-selected steps in fixed ATO order.
    Never changes text. Returns timing adjustment for Scheduler.
    """
    steps = normalize_steps(strategy_steps or plan.strategy_steps or list(ATO_ORDER[:4]))
    assert_ato_order(steps)

    dur = float(plan.duration_ms or speech.predicted_duration or speech.slot_ms)
    slot = float(plan.available_ms or speech.slot_ms or 1)
    adj = TimingAdjustment(speech_uuid=speech.speech_uuid, expected_duration_ms=int(dur))

    for step in steps:
        if step == "trim_silence":
            cut = min(80.0, dur * 0.03)
            dur -= cut
            adj.trim_ms += int(cut)
        elif step == "pause_optimization":
            dur *= 0.96
            adj.pause_ms = max(0, int(slot * 0.02))
        elif step == "prosody":
            dur *= 0.98
        elif step == "tempo":
            # Need shorter audio → tempo > 1
            if dur > slot:
                rate = min(plan.tempo_max, dur / max(1.0, slot))
                adj.tempo = rate
                dur = dur / rate
        elif step == "micro_stretch":
            if dur > slot * 1.02:
                st = min(plan.stretch_max, dur / max(1.0, slot))
                adj.stretch = st
                dur = dur / st
        elif step == "borrow_time":
            need = max(0.0, dur - slot)
            borrow = min(400.0, need)
            adj.borrow_ms = int(borrow)
            dur -= borrow
        elif step == "sentence_merge":
            # Structural — flag for Decision/executor; no local text change
            adj.needs_decision = True
        elif step == "manual_review":
            adj.needs_decision = True
        adj.steps_applied.append(step)
        if dur <= slot * 1.08:
            break

    adj.expected_duration_ms = max(120, int(round(dur)))
    adj.fits = adj.expected_duration_ms <= slot * 1.08
    adj.overflow = adj.expected_duration_ms > slot * 1.08
    adj.underflow = adj.expected_duration_ms < slot * 0.85

    if adj.overflow:
        # P406 — overflow is normal; propose strategies for Decision Layer
        adj.needs_decision = True
        adj.strategies_for_decision = [
            ["trim_silence", "tempo"],
            ["borrow_time"],
            ["sentence_merge", "tempo"],
            ["manual_review"],
        ]
    if adj.underflow:
        # P407 — natural pauses / breath / tempo down — never add words
        adj.breath_ms = min(200, int(slot - adj.expected_duration_ms) // 2)
        adj.pause_ms = max(adj.pause_ms, min(150, int(slot - adj.expected_duration_ms) // 3))
        if adj.tempo > 0.95:
            adj.tempo = max(0.95, adj.tempo * 0.98)
    return adj
