# Workaround Attacks — Self-Attack Against the Contract

> Purpose: enumerate every plausible way to satisfy Meaning Fit V2 on
> paper while still producing wrong results, and, for each, cite the
> code path that already prevents it.

Every attack below is written from the perspective of an adversary who
has read the TZ, wants to pass its literal criteria, but doesn't care
about producing a correct dub. For every attack we cite the exact file
and line range that blocks it.

---

## Attack 1 — Text Truncation

**Attack**. Silently trim the translated sentence's tail so the
predicted TTS duration fits the slot. Leaves a translation ending
with a comma.

**Blocked by**. `engines/semantic_v3/regression_wall.py:355-380`
(`_check_no_text_truncation`) — refuses any adapted sentence whose
`translated_text.rstrip()[-1] == ","` unless `is_enumeration=True`.
`rule="text_truncated"`.

## Attack 2 — Sentence Deletion

**Attack**. Delete short replicas that can't easily be fit ("Yes.",
"No.") and pretend they were never spoken.

**Blocked by**. `engines/semantic_v3/regression_wall.py:126-181`
(`_check_no_disappeared_replicas`) — for every source sentence,
verifies at least half of its non-trivial words appear in the joined
adapted text. `rule="replica_disappeared"`.

## Attack 3 — Replica Relocation

**Attack**. Move a replica by hundreds of milliseconds to reuse an
adjacent, roomier slot.

**Blocked by**. `engines/semantic_v3/regression_wall.py:189-227`
(`_check_no_relocation`) — matches sentences by `sentence_uuid` and
rejects `start_ms` shifts > max(400, 50%) of the source slot.
`rule="replica_relocated"`.

## Attack 4 — Audio Overlap

**Attack**. Let adjacent slots overlap by tens of milliseconds because
"nobody will notice".

**Blocked by** three independent gates that must all be defeated:

1. `engines/semantic_v3/regression_wall.py:200-222`
   (`_check_no_audio_overlap`, `rule="audio_overlap"`).
2. `engines/semantic_v3/absolute_rules.py:72-83`
   (`assert_no_overlap_slots`, invoked after Dub Engine).
3. `engines/dub_engine_v2/detectors.py:70-90` (`detect_tail_spill`
   raises `ArchitectureViolation("P411 Tail Spill")` with
   `hard_fail=True`).

## Attack 5 — Tempo Blow-up

**Attack**. Compress audio by 2× to fit the slot.

**Blocked by**. `engines/dub_engine_v2/timing.py` walks the fixed
strategy ladder — tempo is bounded by
`engines/dub_engine_v2/planning.py` per-plan `tempo_min/max`; any
speech unit that still overflows after the ladder exits sets
`adjustment.needs_decision=True` and is routed to the Decision Policy
via `engines/dub_engine_v2/conflicts.py`. The Regression Wall runs
*before* the Dub Engine, so it never inherits a doctored tempo, and
`engines/semantic_v3/adaptive_planning.py:67-113` caps the audio-side
plan at the `DECISION_ORDER` sequence.

## Attack 6 — Video Stretch

**Attack**. Change the video timebase so the audio fits.

**Blocked by**. `engines/semantic_v3/regression_wall.py:382-410`
(`_check_no_video_stretch`) — any sentence carrying `video_stretch`
or `context["video_stretch"] / context["timebase_shift"]` is rejected
with `rule="video_stretch"`.

## Attack 7 — Artificial Fillers

**Attack**. Append "hmm hmm", "ла ла ла", "тра-та-та" to hit the
duration slot on underflow.

**Blocked by**. `engines/semantic_v3/regression_wall.py:320-352`
(`_check_no_artificial_filler`) — checks `_FILLER_MARKERS`;
`rule="artificial_filler"`.

## Attack 8 — Disabling Checks

**Attack**. Wrap the wall in `try/except ArchitectureViolation: pass`.

**Blocked by**. Two guards:

1. `tests/test_anti_regression_wall.py::TestPhase2WiringIntegration::test_phase2_forbids_silent_wall_disable`
   parses `phase2.py` source and asserts
   `enforce_regression_wall(` appears **before** `lock_all(sentences)`.
2. `engines/semantic_v3/regression_wall.py:97-124`
   raises `ArchitectureViolation` unconditionally when `hard_fail=True`
   (the phase2 caller uses the default).

## Attack 9 — Silent Fallback

**Attack**. Catch `ArchitectureViolation` and continue with the raw
ASR text.

**Blocked by**. `engines/semantic_v3/phase2.py:404-408` calls the
wall with `hard_fail=True` and does **not** catch. Any silent catch
around the pipeline would be visible in code review; the pipeline
integrity contract in `engines/pipeline_integrity/exceptions.py:24-38`
requires the pipeline to stop when integrity is broken.

## Attack 10 — Substitute a New Algorithm Without Root-Cause Fix

**Attack**. Replace Meaning Fit with a shiny new model that fits texts
by generating synonyms; skip Target Duration and re-adaptation.

**Blocked by**. The gates are algorithm-agnostic:

- `engines/semantic_v3/time_equivalence.py:70-140` runs on
  `predicted_tts_ms` vs `slot_ms` — any replacement algorithm still
  has to satisfy the tolerance.
- `engines/semantic_v3/regression_wall.py` runs against the *original*
  projection captured at
  `engines/semantic_v3/phase2.py:143-152`, before Meaning Fit — any
  new algorithm still has to preserve every source replica.
- `engines/semantic_v3/adjacent_scene_check.py:120-170` revalidates
  neighbors regardless of who moved them.

Any substitute algorithm that violates these invariants raises
`ArchitectureViolation` before LOCK.

## Attack 11 — LOCK Before Adaptation

**Attack**. Lock sentences early "for safety" so downstream can't
touch them.

**Blocked by**. `engines/semantic_v3/phase2.py:342-350` explicitly
raises `ArchitectureViolation(rule="lock_before_adaptation")` when a
locked sentence lands in the ЭТАП 7 queue. The runtime log for every
verified scene shows `lockCountBeforeAdaptation: 0`.

## Attack 12 — Weaken the LOCK Guard Later

**Attack**. Set `assert_semantic_rewrite_allowed(threshold=0.0)` so
any rewrite passes.

**Blocked by**. `engines/semantic_v3/semantic_lock.py:44-73` bakes
`entity_preservation` and `numbers` into hard `ArchitectureViolation`
raises — even with the meaning threshold at 0, entities and numbers
must survive. The Dub Engine's immutability check
(`engines/dub_engine_v2/engine.py:130-137`) also refuses any text
change after LOCK.

## Attack 13 — Infinite Re-Adaptation

**Attack**. Keep re-running Meaning Fit until the tolerance is
satisfied.

**Blocked by**. `engines/semantic_v3/time_equivalence.py:70-140` and
its `mark_readaptation_pass` counter — after one extra pass the row
is stamped `reason="exhausted_readaptation_budget"` and `needs_readaptation=False`.
Regression test: `test_caps_at_one_extra_pass`.

## Attack 14 — Route Around Adjacent-Scene Check

**Attack**. Call Meaning Fit re-adaptation directly on a subset,
without snapshotting neighbours, so a slot 2 to the left silently
degrades.

**Blocked by**. `engines/semantic_v3/phase2.py:339-375` — the only
production path for re-adaptation snapshots neighbours and invokes
`revalidate_neighbors_or_revert`. Any bypass would go around
`phase2.run_semantic_v3_phase2`, which is the single entry point
enforced by `engines/semantic_v3/__init__.py:42-46`.

## Attack 15 — Fake the Debug Log to Look Passing

**Attack**. Emit "Regression Wall verdict = passed" without actually
running the wall.

**Blocked by**. The wall's verdict is recorded from `wall_report`, a
dataclass built by `enforce_regression_wall` in the same call site
(`engines/semantic_v3/phase2.py:404-423`). There is no separate
"summary" step; the debug log payload is derived directly from the
wall's `RegressionWallReport`. Removing the call would break the
regression tests (§ Attack 8).

---

## Summary

Every "clever" way to satisfy Meaning Fit V2 on paper while producing
wrong output is either (a) directly rejected by a named
`ArchitectureViolation` rule, or (b) exposed by a test that reads
`phase2.py` source. There is no silent success path.
