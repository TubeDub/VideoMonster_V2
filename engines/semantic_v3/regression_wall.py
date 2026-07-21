"""ЭТАП 10 — Anti-Regression Wall.

A single-point, deterministic gate that runs at the boundary between the
Meaning Fit stage (phase 2) and Dub Engine v2. It refuses to promote a
project past LOCK when any of the historical Meaning Fit failure modes
can be observed on the current in-memory state:

    * a source replica / word / whole sentence disappeared
    * two adjacent replicas overlap in time
    * a replica has been relocated to a different slot
    * a stale WAV / Timeline / Slot / text mismatch is present
    * a replica is scheduled to play twice (duplicate playback)

The wall does not "repair" anything — that would only relocate the
problem, which is exactly the class of false fix ADR-016 rejects. On any
match it raises :class:`ArchitectureViolation` with a stable ``rule``
tag so tests and Studio QA can pinpoint the exact failure.

The wall is intentionally decoupled from any silent fallback: callers
must not except it. It is the last stop before LOCK, so if it fires,
the correct action is to fix the upstream planner, not to bypass it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.types import SemanticSentence

logger = logging.getLogger("tubedub.semantic_v3.regression_wall")

# ────────────────────────────────────────────────────────────────────────────
# Result / config dataclasses
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class RegressionWallReport:
    """Structured, machine-readable outcome of the wall run."""

    passed: bool = True
    checks_run: int = 0
    checks_passed: int = 0
    violations: list[dict[str, Any]] = field(default_factory=list)
    slot_count: int = 0
    covered_source_words: int = 0
    covered_source_sentences: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "violations": list(self.violations),
            "slot_count": self.slot_count,
            "covered_source_words": self.covered_source_words,
            "covered_source_sentences": self.covered_source_sentences,
        }


# Forbidden outcomes the wall must never let through. These are the
# "rule" values that identify a violation category — do not rename them
# without updating tests, docs and Studio QA.
FORBIDDEN_OUTCOMES = (
    "replica_disappeared",
    "sentence_disappeared",
    "audio_overlap",
    "replica_relocated",
    "stale_wav_path",
    "stale_timeline_slot",
    "stale_locked_text",
    "duplicate_playback",
    "artificial_filler",
    "text_truncated",
    "video_stretch",
)


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def enforce_regression_wall(
    original_sentences: list[SemanticSentence],
    adapted_sentences: list[SemanticSentence],
    *,
    timeline_units: Iterable[Any] | None = None,
    speech_units: Iterable[Any] | None = None,
    hard_fail: bool = True,
) -> RegressionWallReport:
    """ЭТАП 10 — run the anti-regression wall between phase2 and Dub Engine v2.

    ``original_sentences`` is the pre-Meaning-Fit projection of the
    project (source text + slot timing). ``adapted_sentences`` is the
    post-Meaning-Fit list that phase2 is about to LOCK.

    If any forbidden outcome is detected, ``ArchitectureViolation`` is
    raised (unless ``hard_fail`` is False, in which case the report is
    returned so tests can inspect it). Silent success is impossible:
    every check contributes to ``checks_run`` and every failure is
    recorded to ``violations``.
    """
    report = RegressionWallReport(slot_count=len(adapted_sentences))

    _check_no_disappeared_replicas(original_sentences, adapted_sentences, report)
    _check_no_audio_overlap(adapted_sentences, report)
    _check_no_relocation(original_sentences, adapted_sentences, report)
    _check_no_stale_state(adapted_sentences, timeline_units, speech_units, report)
    _check_no_duplicate_playback(adapted_sentences, timeline_units, speech_units, report)
    _check_no_artificial_filler(adapted_sentences, report)
    _check_no_text_truncation(original_sentences, adapted_sentences, report)
    _check_no_video_stretch(adapted_sentences, report)

    report.passed = not report.violations
    if report.passed:
        report.checks_passed = report.checks_run
        logger.info(
            "regression_wall PASSED: %d/%d checks; slots=%d",
            report.checks_passed,
            report.checks_run,
            report.slot_count,
        )
        return report

    report.checks_passed = max(0, report.checks_run - len(report.violations))
    logger.error(
        "regression_wall FAILED: %d violations, first=%s",
        len(report.violations),
        report.violations[0] if report.violations else {},
    )
    if hard_fail:
        first = report.violations[0]
        raise ArchitectureViolation(
            f"Regression Wall rejected transition: {first.get('rule')}: {first.get('message')}",
            stage="regression_wall",
            rule=first.get("rule") or "regression_wall",
            segment_id=str(first.get("segment_id") or ""),
            details={"report": report.to_dict()},
        )
    return report


# ────────────────────────────────────────────────────────────────────────────
# Individual checks
# ────────────────────────────────────────────────────────────────────────────


def _register(
    report: RegressionWallReport,
    *,
    rule: str,
    message: str,
    segment_id: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Record a violation without stopping evaluation (used before hard-fail)."""
    payload: dict[str, Any] = {
        "rule": rule,
        "message": message,
        "segment_id": segment_id,
    }
    if extra:
        payload.update(extra)
    report.violations.append(payload)


def _check_no_disappeared_replicas(
    original: list[SemanticSentence],
    adapted: list[SemanticSentence],
    report: RegressionWallReport,
) -> None:
    """Every source replica (sentence) must be represented in the adapted set.

    Meaning Fit is allowed to merge multiple sentences into one MeaningUnit,
    but the *source* text of each original sentence must remain reachable
    via the ``text`` field of at least one adapted sentence (either as-is
    or as a substring / word overlap).
    """
    report.checks_run += 1
    if not original:
        return

    adapted_texts = [(a.text or "").strip() for a in adapted]
    adapted_joined_lc = " || ".join(t.lower() for t in adapted_texts)
    for src in original:
        src_text = (src.text or "").strip()
        if not src_text:
            continue
        report.covered_source_sentences += 1
        # A source replica is "present" if at least half of its non-trivial
        # words appear in the adapted joined text.
        src_words = [w for w in src_text.split() if len(w) > 2]
        if not src_words:
            if src_text.lower() in adapted_joined_lc:
                report.covered_source_words += 1
                continue
            _register(
                report,
                rule="replica_disappeared",
                message=f"source replica missing from adapted set: {src_text!r}",
                segment_id=src.sentence_uuid,
            )
            return
        hits = sum(1 for w in src_words if w.lower() in adapted_joined_lc)
        report.covered_source_words += hits
        if hits < max(1, len(src_words) // 2):
            _register(
                report,
                rule="replica_disappeared",
                message=(
                    f"source replica words missing from adapted set: "
                    f"{hits}/{len(src_words)} words survived"
                ),
                segment_id=src.sentence_uuid,
                extra={"source_words": len(src_words), "kept": hits},
            )
            return

    # Sentence-count sanity: adapted must contain at least one entry
    # whenever the original had any text.
    if original and not adapted:
        _register(
            report,
            rule="sentence_disappeared",
            message="all adapted sentences were dropped",
        )


def _check_no_audio_overlap(
    adapted: list[SemanticSentence],
    report: RegressionWallReport,
) -> None:
    """Adapted sentence time windows must not overlap by more than 40 ms.

    This mirrors ``assert_no_overlap_slots`` but runs *before* LOCK so
    that the wall can reject a broken plan rather than delegate to
    the Dub Engine (which would fail with a less specific message).
    """
    report.checks_run += 1
    ordered = sorted(adapted, key=lambda s: (s.start_ms, s.end_ms))
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if a.end_ms > b.start_ms + 40:
            _register(
                report,
                rule="audio_overlap",
                message=(
                    f"adjacent slots overlap by {a.end_ms - b.start_ms}ms"
                ),
                segment_id=a.sentence_uuid,
                extra={
                    "prev_end_ms": a.end_ms,
                    "next_start_ms": b.start_ms,
                },
            )
            return


def _check_no_relocation(
    original: list[SemanticSentence],
    adapted: list[SemanticSentence],
    report: RegressionWallReport,
) -> None:
    """A source replica must not have moved to a wholly different slot.

    Meaning Fit is allowed to merge sentences (dropping tail sentence
    identities into the first sentence of a MeaningUnit). We therefore
    match by ``sentence_uuid`` where available; if an adapted sentence
    carries a source uuid but its ``start_ms`` shifted by more than
    ``max(400, 50%)`` of the source slot, treat it as relocated.
    """
    report.checks_run += 1
    if not original or not adapted:
        return
    src_by_uuid = {s.sentence_uuid: s for s in original if s.sentence_uuid}
    for adp in adapted:
        src = src_by_uuid.get(adp.sentence_uuid)
        if src is None:
            continue
        if src.slot_ms == 0 or adp.slot_ms == 0:
            continue
        max_shift = max(400, int(src.slot_ms * 0.5))
        shift = abs(adp.start_ms - src.start_ms)
        if shift > max_shift:
            _register(
                report,
                rule="replica_relocated",
                message=(
                    f"replica shifted by {shift}ms (allowed <= {max_shift}ms)"
                ),
                segment_id=adp.sentence_uuid,
                extra={
                    "src_start_ms": src.start_ms,
                    "adapted_start_ms": adp.start_ms,
                },
            )
            return


def _check_no_stale_state(
    adapted: list[SemanticSentence],
    timeline_units: Iterable[Any] | None,
    speech_units: Iterable[Any] | None,
    report: RegressionWallReport,
) -> None:
    """WAV / Timeline / text must reference the same locked identity.

    In the current phase2 flow this wall runs *before* the Dub Engine
    creates WAV files, so ``wav_path`` will legitimately be empty for
    every unit. The check therefore focuses on the *identity* chain:

    - every timeline unit must reference a speech unit,
    - every speech unit must reference a locked sentence,
    - no timeline unit may reference a WAV that does not match its
      speech uuid (stale reuse).
    """
    report.checks_run += 1
    if timeline_units is None and speech_units is None:
        return

    speech_by_uuid: dict[str, Any] = {}
    for su in speech_units or ():
        uid = getattr(su, "speech_uuid", None) or (
            su.get("speech_uuid") if isinstance(su, dict) else None
        )
        if uid:
            speech_by_uuid[str(uid)] = su

    sentence_uuids = {s.sentence_uuid for s in adapted}
    seen_wavs: dict[str, str] = {}
    for unit in timeline_units or ():
        uid = getattr(unit, "speech_uuid", None) or (
            unit.get("speech_uuid") if isinstance(unit, dict) else None
        )
        if not uid:
            continue
        su = speech_by_uuid.get(str(uid))
        if su is None:
            _register(
                report,
                rule="stale_timeline_slot",
                message=f"timeline unit references missing speech_uuid={uid}",
            )
            return
        sent_uuid = getattr(su, "sentence_uuid", None) or (
            su.get("sentence_uuid") if isinstance(su, dict) else None
        )
        if sent_uuid and str(sent_uuid) not in sentence_uuids:
            _register(
                report,
                rule="stale_locked_text",
                message=(
                    f"speech unit references sentence {sent_uuid} that is not in "
                    f"adapted list"
                ),
            )
            return
        wav = getattr(unit, "wav_path", "") or (
            unit.get("wav_path") if isinstance(unit, dict) else ""
        )
        if wav:
            other = seen_wavs.get(str(wav))
            if other and other != str(uid):
                _register(
                    report,
                    rule="stale_wav_path",
                    message=(
                        f"wav {wav} bound to multiple speech_uuids"
                    ),
                )
                return
            seen_wavs[str(wav)] = str(uid)


def _check_no_duplicate_playback(
    adapted: list[SemanticSentence],
    timeline_units: Iterable[Any] | None,
    speech_units: Iterable[Any] | None,
    report: RegressionWallReport,
) -> None:
    """No adapted sentence may appear twice in the timeline / speech chain."""
    report.checks_run += 1
    if timeline_units is None and speech_units is None:
        return
    seen_sentences: set[str] = set()
    for su in speech_units or ():
        sent_uuid = getattr(su, "sentence_uuid", None) or (
            su.get("sentence_uuid") if isinstance(su, dict) else None
        )
        if not sent_uuid:
            continue
        if sent_uuid in seen_sentences:
            _register(
                report,
                rule="duplicate_playback",
                message=f"sentence {sent_uuid} produced two speech units",
                segment_id=str(sent_uuid),
            )
            return
        seen_sentences.add(str(sent_uuid))

    seen_units: set[str] = set()
    for unit in timeline_units or ():
        uid = getattr(unit, "speech_uuid", None) or (
            unit.get("speech_uuid") if isinstance(unit, dict) else None
        )
        if not uid:
            continue
        if uid in seen_units:
            _register(
                report,
                rule="duplicate_playback",
                message=f"speech_uuid {uid} appears twice on timeline",
            )
            return
        seen_units.add(str(uid))


_FILLER_MARKERS = (
    "er, um",
    "uh…",
    "aha, aha",
    "ehm ehm",
    "гмм гмм",
    "e-e-e",
    "хм-хм-хм",
    "ммм ммм",
    "ла ла ла",
    "тра-та-та",
)


def _check_no_artificial_filler(
    adapted: list[SemanticSentence],
    report: RegressionWallReport,
) -> None:
    """Reject filler text glued on to hit a duration slot.

    This is a deterministic string check for the ADR-021 forbidden
    filler markers: they only ever appear if Meaning Fit degenerated
    into "make the text longer" as a symptom fix.
    """
    report.checks_run += 1
    for s in adapted:
        text = (s.translated_text or s.text or "").lower()
        for marker in _FILLER_MARKERS:
            if marker in text:
                _register(
                    report,
                    rule="artificial_filler",
                    message=f"filler marker {marker!r} present in adapted text",
                    segment_id=s.sentence_uuid,
                )
                return


def _check_no_text_truncation(
    original: list[SemanticSentence],
    adapted: list[SemanticSentence],
    report: RegressionWallReport,
) -> None:
    """The adapted translation must not end with a comma / hanging clause.

    A trailing "," (and no matching enumeration flag) is the classic
    tell of "cut the sentence to fit the slot" — a false fix banned by
    the TZ.
    """
    report.checks_run += 1
    for s in adapted:
        text = (s.translated_text or s.text or "").rstrip()
        if not text:
            continue
        if text[-1] in ",":
            if not s.is_enumeration:
                _register(
                    report,
                    rule="text_truncated",
                    message=(
                        f"adapted text ends with comma without enumeration flag"
                    ),
                    segment_id=s.sentence_uuid,
                )
                return


def _check_no_video_stretch(
    adapted: list[SemanticSentence],
    report: RegressionWallReport,
) -> None:
    """The wall must reject any suggestion to change the video timeline.

    Meaning Fit lives entirely on the audio side; if a caller has
    smuggled a ``video_stretch`` or ``timebase_shift`` flag onto a
    sentence, that is a forbidden fix per ADR-021.
    """
    report.checks_run += 1
    for s in adapted:
        if getattr(s, "video_stretch", None):
            _register(
                report,
                rule="video_stretch",
                message="sentence carries forbidden video_stretch flag",
                segment_id=s.sentence_uuid,
            )
            return
        if isinstance(getattr(s, "context", None), dict):
            if s.context.get("video_stretch") or s.context.get("timebase_shift"):
                _register(
                    report,
                    rule="video_stretch",
                    message="sentence context carries forbidden video mutation flag",
                    segment_id=s.sentence_uuid,
                )
                return
