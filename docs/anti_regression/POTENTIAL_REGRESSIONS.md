# Potential Regressions — 25+ Scenarios and Their Blocking Gates

> Purpose: enumerate concrete regression scenarios covering every
> category in the TZ, and cite (a) what "wrong success" would look
> like, (b) which module could plausibly produce it, and (c) which
> existing invariant or new gate blocks it.
>
> All citations refer to the current tree at
> `c:\Users\serhii\Desktop\VideoMonster_V2`.

## Legend

- **W** — the false success ("looks fine on the surface").
- **M** — module that could produce it.
- **G** — invariant / gate that blocks it (file:line + rule name).

---

### R1. Replica disappears (short slot)

- W: "Yes." is present in ASR but the adapted list has no entry for it.
- M: `engines/semantic_v3/meaning_fit_engine.py` if a merge silently
  drops `unit.sentences[1:]`.
- G: `engines/semantic_v3/regression_wall.py:126-181`
  `rule="replica_disappeared"` — matches source words against the
  joined adapted text and refuses to promote if < 50% of a source
  sentence's non-trivial words survive.

### R2. Whole sentence disappears (long slot)

- W: a long English sentence is compressed to a single dot.
- M: Meaning Fit variant "compact" degenerating to empty text.
- G: same rule as R1. Additionally
  `engines/semantic_v3/stage_validator.py:106-113` (`empty_sentences`
  check) marks any empty text sentence as an error.

### R3. Overflow in slot i is "fixed" by squashing text

- W: text truncated with trailing `,` to hit the slot.
- M: any DSAL polish that trims tail clauses.
- G: `regression_wall._check_no_text_truncation` at
  `engines/semantic_v3/regression_wall.py:355-380`,
  `rule="text_truncated"`.

### R4. Underflow "fixed" with artificial filler

- W: "тра-та-та" or "ла ла ла" appended to a short reply.
- M: variant "expand" strategy in
  `engines/semantic_v3/semantic_adaptation.py:305-371`.
- G: `regression_wall._check_no_artificial_filler` at
  `engines/semantic_v3/regression_wall.py:320-352`,
  `rule="artificial_filler"`.

### R5. Adjacent-slot damage (previous)

- W: fixing slot *i* pushes slot *i-1* into overflow.
- M: `phase2.py` re-adaptation loop.
- G: `engines/semantic_v3/adjacent_scene_check.py:120-170`
  reverts the change; failure reason `prev_fit_degraded`.

### R6. Adjacent-slot damage (next)

- W: fixing slot *i* squeezes slot *i+1*.
- M: same as R5.
- G: same file, reason `next_fit_degraded`.

### R7. Scene budget shrinks

- W: the sum of adjacent slot durations decreases after re-adaptation.
- M: any re-planner touching `start_ms`/`end_ms`.
- G: `engines/semantic_v3/adjacent_scene_check.py:150-165` returns
  `scene_budget_shrunk` and reverts.

### R8. Stale WAV bound to another slot

- W: two timeline units point at `foo.wav`.
- M: Dub Engine reusing WAVs for latency.
- G: `regression_wall._check_no_stale_state` at
  `engines/semantic_v3/regression_wall.py:229-273`,
  `rule="stale_wav_path"`.

### R9. Stale Timeline slot

- W: a timeline unit references a `speech_uuid` that no longer exists.
- M: async scheduler with a stale cache.
- G: same check, `rule="stale_timeline_slot"`.

### R10. Stale locked text

- W: the speech unit references a `sentence_uuid` that has been
  dropped from the adapted list.
- M: post-lock refactor that renumbers sentence uuids.
- G: same check, `rule="stale_locked_text"`.

### R11. Duplicate playback (double voice)

- W: the same sentence produces two speech units.
- M: retry logic that appends instead of replaces.
- G: `regression_wall._check_no_duplicate_playback` at
  `engines/semantic_v3/regression_wall.py:275-317`,
  `rule="duplicate_playback"`. Also
  `engines/semantic_v3/absolute_rules.py:72-83`
  (`assert_no_overlap_slots`) and
  `engines/dub_engine_v2/detectors.py:70-90` (P411 tail spill).

### R12. Audio overlap between slots

- W: adjacent slots overlap by >40 ms.
- M: scheduler tempo compression that exceeds the slot.
- G: `regression_wall._check_no_audio_overlap`
  (`engines/semantic_v3/regression_wall.py:200-222`) *and*
  `engines/semantic_v3/absolute_rules.py:72-83`.

### R13. Replica relocated

- W: the same `sentence_uuid` starts at a completely different time.
- M: a "re-cadence" pass that resets `start_ms`.
- G: `regression_wall._check_no_relocation`
  (`engines/semantic_v3/regression_wall.py:189-227`),
  `rule="replica_relocated"`.

### R14. Video stretch flag smuggled in

- W: sentence context carries `video_stretch: 1.2`.
- M: a well-meaning UX fix.
- G: `regression_wall._check_no_video_stretch`
  (`engines/semantic_v3/regression_wall.py:382-410`),
  `rule="video_stretch"`.

### R15. LOCK before adaptation

- W: `semantic_locked=True` set before Meaning Fit.
- M: any refactor moving `lock_all` earlier.
- G: `engines/semantic_v3/phase2.py:342-350` raises
  `ArchitectureViolation(rule="lock_before_adaptation")` when a locked
  sentence lands in the ЭТАП 7 re-adaptation queue. The pre-LOCK
  metric `lockCountBeforeAdaptation` in the debug log is 0 for every
  verified scene.

### R16. Silent fallback on Meaning Fit failure

- W: Meaning Fit fails; pipeline continues with the raw ASR text.
- M: any `except Exception:` swallowing errors.
- G: `engines/semantic_v3/meaning_fit_engine.py:68-70` falls back to
  `direct` variant *within* Meaning Fit only; the outer pipeline
  never suppresses `ArchitectureViolation`.

### R17. Silent bypass of the Regression Wall

- W: someone wraps `enforce_regression_wall(..., hard_fail=False)`
  and ignores the return.
- M: refactor for tests.
- G: unit test `tests/test_anti_regression_wall.py::TestPhase2WiringIntegration`
  asserts that `enforce_regression_wall(` appears in `phase2.py` and
  runs *before* `lock_all(sentences)`.

### R18. Skipping the ЭТАП 7 loop when tolerance is exceeded

- W: `evaluate_and_mark` returns flags but nobody acts.
- M: refactor that removes the `if time_eq_report.flagged:` block.
- G: `tests/test_anti_regression_wall.py::TestPhase2WiringIntegration::test_phase2_records_wall_verdict`
  asserts `anti_regression.time_equivalence` is present in project meta.

### R19. Infinite re-adaptation loop

- W: sentence keeps failing tolerance, keeps being re-adapted.
- M: same as R18, missing pass counter.
- G: `engines/semantic_v3/time_equivalence.py:70-140` — pass counter
  refuses to flag on the second call:
  `test_time_equivalence_caps_at_one_extra_pass`.

### R20. Entity loss during rewrite

- W: "George" dropped when compacting.
- M: `try_rewrite_v2` / `try_semantic_rewrite_sentence`.
- G: `engines/semantic_v3/semantic_lock.py:39-73` raises
  `ArchitectureViolation(rule="entity_preservation")` when entities
  are lost. `engines/semantic_v3/semantic_adaptation.py:379-402`
  `_check_meaning_preservation` marks the variant `rejected`.

### R21. Number loss during rewrite

- W: "18" dropped from "18-year-old boy".
- M: any compact rewrite.
- G: `engines/semantic_v3/semantic_lock.py:65-73`
  `rule="numbers"` and `semantic_adaptation._check_meaning_preservation`.
  Regression test: `test_phase2_rewrite_v2_preserves_numbers`.

### R22. Negation loss

- W: "не хоче" becomes "хоче".
- M: rewrite / compact.
- G: `engines/semantic_v3/stage_validator.py:288-297` — negation loss
  detected. Regression test:
  `test_meaning_preservation_detects_number_loss`.

### R23. Target Duration ignored (uses raw slot)

- W: a 6-second silent slot expanded a one-word reply.
- M: refactor that drops the `target_duration` metadata.
- G: `engines/semantic_v3/duration_predictor.py:95-109` prefers
  `target_duration.target_ms`; debug log evidence
  `targetDurationUsedEverywhere: true` in `phase2.py:time_equivalence`.

### R24. Sentence merge across dialogue turns

- W: replies from speaker A and B fused into one MeaningUnit.
- M: `meaning_unit_builder._should_merge`.
- G: `engines/semantic_v3/meaning_unit_builder.py:49-67` — different
  `is_dialogue` or different `speaker` blocks merge.
  `engines/semantic_v3/adaptive_planning.py:172-184`
  `can_merge_pair` mirrors the rule.

### R25. Sentence split mid-thought

- W: enumeration split at a comma; second half becomes an orphan.
- M: `meaning_unit_builder._find_split_points`.
- G: `engines/semantic_v3/meaning_unit_builder.py:70-108` — split
  requires ≥8 words on each side and only splits on `;` or the
  restricted `_SPLIT_CONJUNCTIONS` list.

### R26. Cross-scene word timing bleed

- W: word timing outside sentence span.
- M: word engine bug.
- G: `engines/semantic_v3/absolute_rules.py:53-69`
  `assert_no_tail_spill` raises
  `ArchitectureViolation(rule="no_double_audio")`.

### R27. Dub Engine mutating locked text

- W: audio-side "polish" changes translated_text.
- M: Dub Engine ATO.
- G: `engines/dub_engine_v2/engine.py:130-137` snapshots before
  processing and raises `ArchitectureViolation("P420")` on mutation.

### R28. Character consistency flip (formal→informal)

- W: source "Would you kindly" → target "Гей, давай".
- M: Meaning Fit picking a low-quality "cultural" variant.
- G: `engines/semantic_v3/strategy_selection.py:150-192`
  `_score_character_consistency` penalises register flip; the low
  score demotes the variant during ranking.

### R29. Localization quality regression (Latin leak)

- W: target text carries stray English runs like "okay guys wow".
- M: dictionary miss.
- G: `engines/semantic_v3/strategy_selection.py:195-230`
  `_score_localization_quality` penalises `_UK_LATIN_LEAK` runs and
  `_UK_TRANSLIT_MARKERS`.

### R30. Time equivalence tolerance widened

- W: someone raises `tolerance_pct` to 100% so nothing gets flagged.
- M: config drift.
- G: `engines/semantic_v3/time_equivalence.py:25-27` sets a strict
  15% default; the debug log records the actual value used
  (`toleranceHalf: 15.0`) and the regression test
  `test_flags_out_of_tolerance` locks 15% as the observed default.

---

## Categories covered vs. TZ list

| TZ category | Scenarios |
|-------------|-----------|
| short/long | R1, R2 |
| overflow/underflow | R3, R4 |
| disappearance | R1, R2, R16, R20-R22 |
| duplication | R11 |
| adjacent-slot damage | R5, R6, R7 |
| stale WAV / Timeline / text | R8, R9, R10 |
| silent fallback | R16, R17 |
| video stretch | R14 |
| artificial fillers | R4 |
| LOCK-before-adaptation | R15 |
| relocation | R13 |
| audio overlap | R12 |
| infinite loop | R19 |
| meaning preservation | R20, R21, R22 |
| Target Duration missing | R23 |
| character consistency / localization | R28, R29 |

30 scenarios enumerated, exceeding the "at least 25" TZ minimum.
