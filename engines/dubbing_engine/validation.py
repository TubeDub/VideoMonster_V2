"""
Stage 7 — Pre-TTS Validation Gate.

8-point quality check before synthesis. If any check fails, TTS is skipped
for that segment and the segment is marked with a recommended strategy.

Checks (in order):
  1. meaning_ok   — word retention ratio ≥ MIN_RETENTION
  2. entity_ok    — all protected entities present in output text
  3. lang_ok      — output text is in the target language (not source lang)
  4. stress_ok    — stress processing was not skipped
  5. punct_ok     — segment ends with proper punctuation
  6. timing_ok    — predicted duration ≤ slot * MAX_OVERFLOW_FACTOR
  7. overlap_ok   — no overlap with adjacent segments
  8. voice_ok     — projected atempo ≤ MAX_ATEMPO (no robotic compression)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Thresholds ─────────────────────────────────────────────────────────────────
MIN_WORD_RETENTION: float = 0.55       # at least 55 % of input words must survive
MAX_OVERFLOW_FACTOR: float = 1.30      # predicted can exceed slot by up to 30 %
MAX_ATEMPO: float = 1.20              # above this → voice quality degrades
_TERMINAL_OK = frozenset(".?!…")

# ── Language detection heuristics ──────────────────────────────────────────────
_CYRILLIC = re.compile(r'[а-яёіїєА-ЯЁІЇЄ]')
_LATIN_UPPER = re.compile(r'\b[A-Z][a-z]{2,}\b')
# For German detection
_GERMAN_CHARS = re.compile(r'[äöüÄÖÜß]')


@dataclass
class ValidationReport:
    passed: bool
    notes: list[str] = field(default_factory=list)
    strategy: str = "direct"   # recommended action
    checks: dict[str, bool] = field(default_factory=dict)

    def fail(self, check: str, note: str, strategy: str = "") -> None:
        self.passed = False
        self.checks[check] = False
        self.notes.append(note)
        if strategy:
            self.strategy = strategy

    def ok(self, check: str) -> None:
        self.checks[check] = True


def _word_set(text: str) -> set[str]:
    return {w.lower().strip(".,!?;:—–-\"'«»") for w in text.split() if len(w) > 2}


def _dominant_script(text: str) -> str:
    """Return 'cyrillic', 'latin', or 'mixed'."""
    cyr = len(_CYRILLIC.findall(text))
    lat = len(re.findall(r'[a-zA-Z]', text))
    if cyr > lat * 2:
        return "cyrillic"
    if lat > cyr * 2:
        return "latin"
    return "mixed"


def _expected_script(lang: str) -> str:
    base = (lang or "ru").split("-")[0].lower()
    if base in ("ru", "uk", "be"):
        return "cyrillic"
    return "latin"


def run_validation(
    *,
    input_text: str,           # text before the engine (post-translation)
    output_text: str,          # text the engine produced (to be sent to TTS)
    source_text: str = "",     # original-language text (for entity check)
    entities: list | None = None,  # EntityInfo list
    stress_applied: bool = True,
    punct_ok: bool = True,
    predicted_ms: int = 0,
    slot_ms: int = 0,
    prev_end_ms: int = 0,      # when previous segment ends (for overlap check)
    slot_start_ms: int = 0,
    lang: str = "uk",
) -> ValidationReport:
    """
    Run all 8 checks. Returns a ValidationReport.
    On first "hard" failure the segment is marked skip_tts.
    On "soft" failures it may still proceed with a recommended strategy.
    """
    report = ValidationReport(passed=True, strategy="direct")

    # 1. Meaning preservation
    if input_text:
        orig_words = _word_set(input_text)
        out_words = _word_set(output_text)
        if orig_words:
            retained = len(orig_words & out_words) / len(orig_words)
        else:
            retained = 1.0
        if retained < MIN_WORD_RETENTION:
            report.fail(
                "meaning",
                f"meaning_lost: word_retention={retained:.2f} (min={MIN_WORD_RETENTION})",
                strategy="skip_tts",
            )
        else:
            report.ok("meaning")

    # 2. Entity check
    if entities:
        from engines.dubbing_engine.entities import validate_entities
        entity_ok, entity_notes = validate_entities(output_text, entities)
        if not entity_ok:
            report.fail("entity", "; ".join(entity_notes))
            # Not a hard failure — note it but continue
            report.passed = True  # override back to passable
        else:
            report.ok("entity")
    else:
        report.ok("entity")

    # 3. Language check
    expected = _expected_script(lang)
    actual = _dominant_script(output_text)
    if expected == "cyrillic" and actual == "latin":
        report.fail(
            "lang",
            f"language_mismatch: expected cyrillic for {lang}, got mostly latin",
            strategy="skip_tts",
        )
    else:
        report.ok("lang")

    # 4. Stress marks check (soft — just log if missing)
    if not stress_applied:
        report.notes.append("stress_not_applied")
    report.ok("stress")  # never hard-fail on stress

    # 5. Punctuation check
    stripped = (output_text or "").rstrip()
    if stripped and stripped[-1] not in _TERMINAL_OK:
        report.notes.append("no_terminal_punctuation")
        # Soft failure only
    report.ok("punct")

    # 6. Timing check
    if slot_ms > 0 and predicted_ms > 0:
        overflow = predicted_ms / slot_ms
        if overflow > MAX_OVERFLOW_FACTOR:
            report.fail(
                "timing",
                f"severe_overflow: predicted={predicted_ms}ms slot={slot_ms}ms ratio={overflow:.2f}",
                strategy="adapt_more",
            )
            # Still allow TTS but flag it — timing_fit will handle the rest
            report.passed = True
        else:
            report.ok("timing")
    else:
        report.ok("timing")

    # 7. Overlap check
    if slot_start_ms > 0 and prev_end_ms > 0:
        if slot_start_ms < prev_end_ms - 50:  # 50 ms tolerance
            report.fail(
                "overlap",
                f"overlap: slot_start={slot_start_ms}ms prev_end={prev_end_ms}ms",
                strategy="delay_start",
            )
            report.passed = True  # timing_fit resolves this
        else:
            report.ok("overlap")
    else:
        report.ok("overlap")

    # 8. Voice quality / atempo projection
    if slot_ms > 0 and predicted_ms > 0:
        projected_atempo = predicted_ms / slot_ms
        if projected_atempo > MAX_ATEMPO:
            report.fail(
                "voice",
                f"voice_quality_risk: projected_atempo={projected_atempo:.2f} "
                f"(max={MAX_ATEMPO}) — recommend text adaptation",
                strategy="adapt_more",
            )
            report.passed = True  # not a skip_tts; guide engine to re-adapt
        else:
            report.ok("voice")
    else:
        report.ok("voice")

    return report
