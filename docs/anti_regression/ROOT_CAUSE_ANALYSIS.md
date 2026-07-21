# Root Cause Analysis — Meaning Fit V2 Historical Failures

> Session id: `7e57dc` · Log file: `debug-7e57dc.log`.
> Scope: the actual pre-P50 Meaning Fit failure modes that produced
> disappeared replicas, silent LOCK-before-adaptation, missing Target
> Duration and one-way adaptation. Every finding cites concrete files
> and line ranges in the repository as it stands after this
> anti-regression contract landed.

## 1. Real root cause (one sentence)

The historical Meaning Fit failures were **not** an audio-scheduling
problem — they were a **decision-ownership** problem: Whisper-owned ASR
segments were treated as authoritative units, LOCK ran before
adaptation, no `Target Duration` was computed for text fitting, and the
adaptation loop was one-way (source→target, never original↔adapted),
so once a bad choice was made nothing revisited it.

## 2. Failure symptoms observed historically

- **"Yes." expanded into a paragraph**, or a long English sentence
  crammed into 800 ms.
- **George Lucas father-son fragment** (`tests/golden/dub/george_lucas_en_uk_20.json`)
  segment 6: `must_restore: ["між батьком і сином"]` — the phrase was
  dropped in-flight and never restored.
- **Adjacent-slot damage**: fixing overflow in slot *i* pushed slot
  *i+1* into overflow.
- **Stale WAV / Timeline / text** where the timeline pointed to a WAV
  from a previous plan.
- **Silent LOCK before adaptation**: `translation_lock` was applied,
  then any downstream rewrite was suppressed instead of raising.

## 3. Where the real root cause lives in code

### 3.1 Whisper-owned segments treated as decision units

Historically, ASR archive rows were fed straight into the Dub Engine
timeline. The current codebase blocks that by construction, but the
Whisper-owner boundary is still asserted at:

- `engines/semantic_v3/phase2.py:96-107` — `run_semantic_core` is the
  only entry point that turns ASR text/timing into `SemanticWord` and
  `SemanticSentence` objects; the raw ASR archive lives in `archive`
  and is never used for timing decisions.
- `engines/semantic_v3/stage_validator.py:225-254`
  (`validate_no_segment_rule`) — hard rejects `unit_type ==
  "whisper_segment"` and forbidden keys `chunk`, `buffer`, `window`,
  `segment_id`.
- `engines/semantic_v3/phase2.py:344` — `"whisper_owner": False`
  is stamped on every project; any regression that flips it will be
  observable via `meta["whisper_owner"]`.

The historical failure was that the pipeline *did* let Whisper
segments drive timing. Fixing "the symptom" by adjusting the audio
scheduler moved the problem into the Dub Engine; the real fix is that
`Whisper` supplies word timestamps and nothing else
(`engines/semantic_v3/phase2.py:86-107`).

### 3.2 LOCK-before-adaptation

The historical bug was: `lock_all_meaning_units` (see
`engines/semantic_v3/meaning_lock.py:23-92`) ran with `force=True`
before the Meaning Fit engine chose a variant. Any later attempt to
rewrite was silently absorbed by
`engines/semantic_v3/adaptation.py:59-70` (`try_semantic_rewrite_sentence`
returns the original when the LOCK guard fails — a classic "silent
success").

The current pipeline enforces the reverse order:

- `engines/semantic_v3/phase2.py:143-151` — Meaning Fit V2 is called on
  **unlocked** sentences (verified live via the debug log line
  `phase2.py:105 Translation lock state immediately after translation`
  → `lockedCount: 0`).
- `engines/semantic_v3/phase2.py:335-350` — ЭТАП 9 re-adaptation
  refuses to touch anything already locked and raises
  `ArchitectureViolation(rule="lock_before_adaptation")`.
- `engines/semantic_v3/phase2.py:425-439` — `lock_all` is the last
  step before the Dub Engine, after every gate has passed. If any
  future refactor moves LOCK earlier, the ЭТАП 7 guard fires and
  refuses to proceed.

### 3.3 Missing Target Duration

Historically the pipeline used the *physical slot* (`end_ms −
start_ms`) as the target. That value contains surrounding silence, so
a one-word "Yes." with a 6-second silent slot triggered ridiculous
expansion.

The fix lives in `engines/semantic_v3/target_duration_engine.py:35-89`:

```35:89:engines/semantic_v3/target_duration_engine.py
def compute_target_duration(
    unit: Any,
    *,
    translated_text: str = "",
    tolerance_pct: float = 12.0,
) -> TargetDuration:
    """Derive Target Duration from source cadence and the available window.
    ...
    """
```

The engine clamps the observed syllable rate to `2..7` syll/s (so
implausibly slow ASR silence is ignored), adds punctuation dwell time,
and picks `min(cadence_ms, available_ms)` as the speech target. That
number — not the raw slot — is what Meaning Fit fits to
(`engines/semantic_v3/meaning_fit_engine.py:34-58`).

`engines/semantic_v3/duration_predictor.py:95-109` then uses the
`target_duration` metadata (falling back to the physical slot only if
the target isn't set) so overflow/underflow are computed against the
speech target, not the raw slot.

The debug log corroborates this: every scene emits
`targetDurationUsedEverywhere: true` at
`phase2.py:time_equivalence`.

### 3.4 One-way adaptation loop

Historically adaptation was source → target only: pick a variant,
LOCK, done. Nothing compared the *predicted adapted duration* against
the *original slot* and re-ran adaptation when the delta was too
large.

The new gate implements ЭТАП 7 in
`engines/semantic_v3/time_equivalence.py:1-140`:

- `evaluate_and_mark` computes `original_duration_ms →
  adapted_duration_ms → delta_ms/pct`.
- Any row exceeding tolerance is stamped `needs_readaptation`.
- `mark_readaptation_pass` bumps a per-sentence counter so the loop
  caps at exactly **one** extra Meaning Fit pass — the historical TZ
  ЗАДАЧА №10 requirement.

`engines/semantic_v3/phase2.py:335-399` wires this into the pipeline:
after Meaning Fit, if any sentence exceeds tolerance, that subset is
routed back through Meaning Fit once, revalidated against the
neighbourhood
(`engines/semantic_v3/adjacent_scene_check.py:120-170`), and *reverted*
if a neighbor's fit degrades. There is no third pass.

## 4. False fixes we explicitly rejected

These are fixes that make the *symptom* disappear but relocate the
problem. Every one is now blocked by an anti-regression gate; each
gate raises `ArchitectureViolation` with a stable `rule` name
(`engines/semantic_v3/regression_wall.py:46-63`).

| False fix | What it looks like | Where it is now blocked |
|-----------|--------------------|-------------------------|
| Audio-only overflow strategies (tempo / stretch / borrow) hiding text mistakes | Meaning Fit skipped, Dub Engine forced to compress audio | `engines/semantic_v3/adaptive_planning.py:67-113` still allows audio steps *but* the Regression Wall (`regression_wall.py:81-115`) verifies text integrity BEFORE Dub Engine sees anything, so audio steps can never mask disappearance |
| DSAL fillers ("hmm hmm", "тра-та-та") padding a short target | Underflow "fixed" by inserting nonsense | `regression_wall.py:_check_no_artificial_filler` (`engines/semantic_v3/regression_wall.py:320-352`) rejects known filler markers with `rule="artificial_filler"` |
| Silent LOCK before adaptation | Rewrite attempts return early with the original text | `engines/semantic_v3/phase2.py:342-350` raises `ArchitectureViolation(rule="lock_before_adaptation")` if a locked sentence lands in the re-adaptation queue |
| Cutting the sentence with a trailing comma to fit | Translation ends `,` with no enumeration flag | `regression_wall._check_no_text_truncation` (`engines/semantic_v3/regression_wall.py:355-380`) rejects with `rule="text_truncated"` |
| Video stretch / timebase shift | Change the video timeline to give more room | `regression_wall._check_no_video_stretch` (`engines/semantic_v3/regression_wall.py:382-410`) rejects with `rule="video_stretch"` |
| Duplicate playback ("play the WAV twice") | Timeline emits the same speech_uuid twice | `regression_wall._check_no_duplicate_playback` (`engines/semantic_v3/regression_wall.py:275-317`) rejects with `rule="duplicate_playback"` |
| Stale WAV bound to another slot | Two timeline units point at the same WAV | `regression_wall._check_no_stale_state` (`engines/semantic_v3/regression_wall.py:229-273`) rejects with `rule="stale_wav_path"` |

## 5. Fixes that only relocate the problem — explicitly not applied

The following "shortcuts" were considered and rejected because they
would only move the problem into another module:

- **"Just make Meaning Fit greedier"** — would starve the adjacent
  slot; blocked by `adjacent_scene_check.revalidate_neighbors_or_revert`
  (`engines/semantic_v3/adjacent_scene_check.py:120-170`).
- **"Weaken the LOCK guard so downstream can rewrite"** — would allow
  post-lock text mutation, i.e. re-introduce the exact historical
  failure. The Dub Engine's immutability check
  (`engines/dub_engine_v2/engine.py:130-137`) already raises
  `ArchitectureViolation("P420")` on text mutation.
- **"Retry Meaning Fit until it fits"** — infinite loop risk; capped
  to one extra pass in
  `engines/semantic_v3/time_equivalence.py:70-140`.
- **"Substitute a new algorithm without root-cause fix"** — no new
  algorithm is silently swapped in; the gates fail loudly so any
  future replacement must satisfy the same contract.

## 6. Summary

Root cause: **decision ownership** — Whisper owned segments, LOCK ran
before adaptation, Target Duration was absent, and the adaptation loop
was one-way. Each of those points is now enforced structurally by a
named gate that raises `ArchitectureViolation` on breach. False fixes
that only shift the problem are itemised in §4 with the exact code
that rejects them.
