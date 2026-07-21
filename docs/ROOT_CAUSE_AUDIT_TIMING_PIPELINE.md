# Root Cause Audit — Adaptation → Timing → Render Pipeline

**Date:** 2026-07-13  
**Source evidence:** Desktop `g.json` (task `66651127645a43958106ccd4d700fb9c`), studio session same id, code paths in `auto_dub_api` / `timing_fit` / `scheduler`  
**Scope:** Audit only — **no Dub Engine business-logic fix** in this phase  
**Artifacts:** `_tmp_timing_audit/g_timing_audit.json`, `seg16.json`, `seg16_studio.json`, `pre_merge_66651127645a43958106ccd4d700fb9c.json`

---

## Executive verdict (evidence-backed)

**Primary root cause:** after `ADAPTATION EXECUTED`, the pipeline **does not re-bind the Scheduler/Render timeline to the real post-adaptation audio duration**. Slot edges stay on the original Whisper window; Render places the (longer) WAV at the original `start` from `timing_map` and **overlays** it onto a continuous track — so audio **bleeds into the next segment** (overlap / perceived clipping / wrong assembly).

`ADAPTATION EXECUTED` here means **“overflow strategy stamped / decision logged”**, not **“overflow resolved and timeline updated”**.

---

## Stage map (timing lifecycle)

| Stage | What sets time | Duration source | Evidence |
|-------|----------------|-----------------|----------|
| Whisper / Sentence | `timing_map` start/end | ASR slot | OpenDDF `start_time_ms`/`end_time_ms` |
| Translation / Adaptation Decision | stamps `adaptation_executed`, Decision Trace | predicted / decision overflow | `adaptation_decision`, `decision_trace` |
| TTS | `tts_ms` / file length | real WAV/MP3 | studio `tts_ms` |
| ATO / Closed loop | may trim/pause; may call `register_overflow` | `actual_duration_ms` | code map |
| Slot Fit | writes `fitted_file`/`fitted_ms`/`overflow_ms`; then `_scheduler_set_segment_slot(start, end)` with **original slot** | fitted or raw TTS | `auto_dub_api.py` ~2598–2600 |
| Scheduler | owns `start_ms`/`end_ms` | **still Whisper slot** after fit | studio seg16: 127920–138800 |
| Merge / Render | `_build_timed_dub_track` places from **`timing_map`**, not Scheduler edges (unless `merge_adjusted_start` / `tts_timing`) | measured file length | `auto_dub_api.py` ~3007–3016; `timing_fit.build_gap_adjusted_track` overlay |

---

## Answers to TZ questions

### 1. Where do timings first change after Adaptation?

**Slot edges (`start_ms`/`end_ms`) largely do not change after Adaptation** for the failing segments.

- Studio seg16: `start_ms=127920`, `end_ms=138800` (= Whisper slot 10880 ms).
- Real audio: `tts_ms=15561`, `fitted_ms=15541` → still ~4.5–4.7 s over slot.
- `_scheduler_set_segment_slot(seg, start_ms=start_ms, end_ms=end_ms)` after slot_fit **re-writes the original slot**, not `end = start + fitted_ms`.

First *semantic* post-adaptation change is usually **`fitted_ms` / `overflow_ms` / `fitted_file` / `adaptation_executed`**, not the timeline edges.

### 2. Why can `overflow_ms` look like 0 after Adaptation?

In **this** `g.json` run, overflow is **not** globally zero:

| Metric | Count |
|--------|------:|
| segments | 20 |
| `adaptation_executed` | 20 |
| reported `overflow_ms > 0` | **10** |
| derived TTS−slot > 0 | **10** |
| audio bleed into next slot | **10** |
| false “reported 0 but still overflows” | 0 (among rows with final_tts) |

So for the user-visible failure mode, **overflow often remains > 0** while status says EXECUTED.

When zero appears, evidence supports these meanings (not mutually exclusive):

1. **True fit** — TTS ≤ slot (rare in this run’s problem set).
2. **Stale / missing OpenDDF duration** — some rows have `final_tts_duration_ms=0` → reported overflow 0 with incomplete fields.
3. **Decision snapshot vs post-fit field** — Decision Trace transitions show intermediate `overflow_ms=0` after a compress stamp, then later stages still show thousands of ms (seg16 transitions).
4. **`ADAPTATION EXECUTED` stamped on strategy choice** (`pause_optimization`, `trim_silence`, `video_adapt`) **without requiring overflow→0**.

Seg16 Decision Trace ends with `final_result=SUCCESS(pause_optimization)` while `detail.overflow_ms=2271` and OpenDDF `overlap_info.overflow_ms=4730`.

### 3. Does Scheduler use updated values?

**It stores the original Whisper slot after slot_fit**, not the adapted audio end.

- Function: `_scheduler_set_segment_slot` ← `Scheduler.update_time`
- File: `api/auto_dub_api.py` (post slot_fit finalize)
- Old/new: start/end remain Whisper anchors (studio proof)

ATO *can* call `update_time` for gap redistribute, but that did not fix seg16 (edges unchanged; container `red`).

### 4. Does Merge use updated values?

**Placement start** comes from:

1. `tts_timing` if present, else
2. **`timing_map` (Whisper)**, else
3. optionally `merge_adjusted_start`

It does **not** prefer `seg["start_ms"]`/`end_ms` from Scheduler for placement.

Duration for mix = **measured audio file** (`fitted_file` then `file`), not `original_duration_ms`.

### 5. Does Render use `original_duration_ms`?

**No for mix length.** Render uses measured WAV/MP3 length.

`original_duration_ms` in OpenDDF for seg16 equals **slot** (10880), i.e. pre-TTS slot length — diagnostic/slot fallback, not the mix clock. The failure is **overlay of longer real audio on unchanged Whisper starts**, not “render reads original_duration_ms as clip length”.

### 6. First divergence: calculated vs actual

**First clear divergence:** after TTS/slot_fit:

- Slot / Scheduler / timing_map end = Whisper end  
- Real audio end = `start + fitted_ms` ≫ Whisper end  

OpenDDF bleed example (seg16→17):

- slot end 138800  
- audio end 127920+15610 = **143530**  
- bleed **4730 ms** into next segment  
- still `adaptation_status = ADAPTATION EXECUTED`

---

## UUID / object consistency (Stage 4)

From studio/OpenDDF for the sample segment, identity fields present (`segment_id`, files). No evidence that the wrong TTS UUID was mixed for another segment’s text in this audit slice.

The failure is **timeline binding**, not UUID swap:

`segment` → same `fitted_file` / `file`, but **scheduler_slot / render_clip stay on Whisper window while audio duration does not**.

---

## Functions that mutate start/end/duration (inventory)

| Module | Function | Fields | Notes |
|--------|----------|--------|-------|
| `engines/scheduler/api.py` | `update_time` / `request_time` | `start_ms`,`end_ms`,`place_start`,… | Sole owner of edges |
| `api/auto_dub_api.py` | `_scheduler_set_segment_slot` | edges + `slot_ms` | Re-applies **original** slot after fit |
| `api/auto_dub_api.py` | `_prepare_segment_audio_for_mux` | `fitted_ms`,`overflow_ms`,`fitted_file` | Does not extend `end_ms` |
| `api/auto_dub_api.py` | `_build_timed_dub_track` | placement from **timing_map** | Ignores Scheduler edges by default |
| `engines/timing_fit.py` | `build_gap_adjusted_track` | mix overlay at `place_start` | Full audio length; bleed possible |
| `engines/audio_timing_optimizer.py` | `optimize_project` | edges via Scheduler | Optional redistribute |
| `engines/closed_loop_timing.py` | measure/regen | `tts_ms`,`actual_duration_ms` | Duration fields |
| `engines/ai_adaptation_engine.py` | `propagate_adaptation_flags` | `original_duration_ms` | Pre-TTS stamp |

Full readonly inventory also in explore notes from this session.

---

## Problematic segment log (Adaptation → Render)

**Segment #16** `ec5653a68d1746118006497d376099d5`

```
Adaptation Decision:
  executed=true decision=pause_optimization
  adaptation_decision.overflow_ms=2271
Decision Trace:
  strategy SUCCESS (pause_optimization)
  tts_duration SKIPPED (AudioStrategyNoTextRewrite)
  scheduler SUCCESS (strategy_queued)  ← queued, not “overflow cleared”
OpenDDF:
  start=127920 end=138800 slot=10880
  final_tts=15610 overflow_ms=4730 slot_overflow=true
Studio:
  start_ms=127920 end_ms=138800
  tts_ms=15561 fitted_ms=15541 overflow_ms=4586
  fitted_file=slot_fit_66651127_…wav
  container_status=red
Render implication:
  place_start ← timing_map start (127920)
  audio_len  ← ~15541
  next_start ← 138800
  → bleed ~4.7s into next segment
```

---

## Root causes (ranked)

1. **Timeline not updated after adaptation** — Scheduler/Render keep Whisper `end_ms`; real audio longer → bleed/overlap. **CONFIRMED** (g.json + studio + code).
2. **`ADAPTATION EXECUTED` means decision stamped, not overflow resolved** — SUCCESS Decision Trace with remaining overflow. **CONFIRMED** (seg16 Decision Trace + overflow_ms>0).
3. **Render placement source is `timing_map`, not post-adaptation Scheduler edges** — **CONFIRMED** in code; runtime H3 logs added for next mix run.
4. **Mix uses overlay of full WAV** (`build_gap_adjusted_track`) without requiring fit-to-next-start — **CONFIRMED**.
5. **Render uses `original_duration_ms` as clip length** — **REJECTED** for mix path (uses measured file).

---

## Instrumentation added (diagnostics only)

- `engines/pipeline_integrity/timing_lifecycle_audit.py` — Stage 8 pre-merge dump  
- Hook after slot_fit → `task.info.timing_lifecycle_audit`  
- Hook at start of `_build_timed_dub_track` + H3 placement-source NDJSON → `debug-ee98a6.log`

**No business-logic change** to strategy selection / SUCCESS gate semantics beyond diagnostics.

---

## Recommended fix direction (next phase — not done here)

1. Define `ADAPTATION EXECUTED` only when post-fit `overflow_ms==0` **or** explicitly `video_adapt`/`gap_absorb` with documented absorb contract.  
2. After real audio duration known: either (a) fit/trim/tempo until slot, or (b) `Scheduler.update_time(end_ms=start+audio)` + shift neighbors, or (c) hard-trim mix to next_start with logged SKIP/FAIL.  
3. Make `_build_timed_dub_track` place from Scheduler edges (or assert timing_map == scheduler).  
4. Keep regression: EXECUTED + bleed_risk must fail QA.

---

## Criterion

Work for this TZ phase is complete: **one primary root cause + supporting causes**, backed by `g.json`, studio session, and code citations — not speculation.

---

## Fix applied (post-audit, 2026-07-13)

**Bug:** `_build_gap_adjusted_track_no_double_soft_sync` set `no_speech_trim=pre_fitted`, so slot-fitted WAVs that still overflowed were overlaid at full length into the next segment.

**Change:**
1. `no_speech_trim` only when `video_adapt` / `gap_absorb` (explicit absorb).
2. Pre-fitted still skips soft-sync/re-atempo (`_skip_soft_sync`), but hard-cap trim to `next_start` runs.
3. Render placement prefers Scheduler `start_ms`/`end_ms` over raw `timing_map`.
