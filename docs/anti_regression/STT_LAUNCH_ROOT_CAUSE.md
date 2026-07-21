# STT Launch Root Cause — Meaning-First Pipeline `segment_count = 0`

**Task:** `ed4098575cc3483686757de5a2a60106`  
**Evidence:** `л.json`, `л.zip` (`stacktrace.txt`, `report.json`, `audio_extraction_report.json`)  
**Date:** 2026-07-14

---

## 1. Single first-cause

**`UnboundLocalError` on `persist_to_task_info` at `api/auto_dub_api.py:7410` (pre-fix line; now ~7685).**

`persist_to_task_info` was imported only inside the `if word_timing_enabled:` branch (`api/auto_dub_api.py:7267–7272`). When the initial `word_timing` flag path was skipped but Semantic V3's forced-WTM branch later set `word_timing_enabled = True` and populated `merged_word_maps`, the post-merge persist block referenced a name that was never bound in that execution path → `UnboundLocalError` → STT stage crash → `segment_count = 0`, all AI agents `not_called`, Meaning-First never entered.

**Fix (minimal):** local import at persist call site:

```python
from engines.word_timing import persist_to_task_info as _persist_wtm_to_task_info
_persist_wtm_to_task_info(task["info"], merged_word_maps, timing_map=timing_map)
```

(`api/auto_dub_api.py:7681–7688`)

---

## 2. Evidence chain: last-known-good → first missing

| Checkpoint | Object | State in failing run |
|---|---|---|
| **Last known-good** | Extracted audio file | `output\sessions\ed409857…\ed409857_extracted.mp3` — `audio_extraction_report.json: success=true`, ffmpeg returncode 0 |
| **Last known-good** | Whisper/STT invocation | `pipeline.log`: `stage_begin STT` → `stage_complete STT` (48.5s) — Whisper ran and completed |
| **Last known-good** | `source_text` / `raw_lines` | STT completed with non-empty text (stage_complete OK; empty check at line 7195 did not fire) |
| **First missing** | `task["info"]["source_segments"]` | Never written — crash at persist block before `task["info"]["source_segments"] = list(seg_lines)` |
| **First missing** | `segments` / `segment_count` | `л.json` → `summary.segment_count: 0`, `segments: []` |
| **First missing** | `launch_decision_trace` / Semantic V3 | Pipeline aborted in STT stage; `ai_core_report.agents.*.status: "not_called"` |

---

## 3. Refutation of false root causes

### False RC-1: "Whisper returned zero segments"

**Refuted.** `pipeline.log` line 8: `stage_complete → Status: OK | stage: STT`. `ddf_report.json` records `Whisper/STT called=true, success=true`. Empty-STT guard at `api/auto_dub_api.py:7195` (`STT_EMPTY`) did not trigger. Segments existed in memory but were never persisted.

### False RC-2: "`semantic_v3` feature flag disabled"

**Refuted.** `data/feature_flags.json:162–174` → `semantic_v3.enabled: true`. Crash occurred before the Semantic V3 gate at `api/auto_dub_api.py:7489` (`semantic_v3_enabled()`). `л.json` shows no `semantic_v3` block in task info — pipeline never reached the gate.

### False RC-3: "Audio extraction failed — no input for STT"

**Refuted.** `audio_extraction_report.json`: `success: true`, `ffmpeg_returncode: 0`, output `ed409857_extracted.mp3` (524 KiB). `ddf_report.json` agent `AudioExtraction: success=true`. STT used `dialogue_stt_path` pointing at the extracted file (`л.json` → `source_separation.dialogue_stt_path`).

---

## 4. Data-ownership table

| Object | Creator | Mutator(s) | Allowed to clear |
|---|---|---|---|
| **Words** | `engines/semantic_v3/word_engine.py` (`build_words_from_timing_map`) via `run_semantic_core` | `align_words_to_sentences`, WTM `sync_timing_map` | None after Semantic LOCK |
| **Sentences** | `engines/semantic_v3/sentence_builder.py` via `run_semantic_core` | Meaning Fit, adaptive rewrite, `dynamic_sentence_merge`, `lock_all` | None after LOCK (`semantic_lock.py`) |
| **MeaningUnits** | `engines/semantic_v3/meaning_unit_builder.py` | `fit_meaning_units_to_target` (pre-LOCK only) | None after LOCK |
| **Segments (orchestrator)** | `api/auto_dub_api.py` STT merge (`segment_merger`) | Semantic V3 `phase2_to_orchestrator_arrays` export | Only on new task / explicit restart |
| **Timeline** | `engines/dub_engine_v2` / `engines/semantic_v3/scheduler_v2.py` | Dub Engine v2 post-LOCK | None during active dub session |

Whisper/STT owns **ASR archive only** (`unit_type: asr_archive_only`); it must not own Sentences, MeaningUnits, or Segments (`engines/semantic_v3/phase2.py:130`).

---

## 5. Silent-exit hardening audit (STT → Semantic V3 handoff)

| File:line | Before | After | Rationale |
|---|---|---|---|
| `api/auto_dub_api.py:7121` | `except Exception: stt_word_timestamps = False` (silent) | Trace `STT Started/SKIPPED reason=word_timing_flag_lookup_failed:<Type>` | Names flag-lookup failure |
| `api/auto_dub_api.py:7195` | `return _fail(..., STT_EMPTY)` only | + trace `STT Started/FAILED`, `Words Built/SKIPPED`, `Meaning Pipeline/SKIPPED` | Explicit stage ledger on empty STT |
| `api/auto_dub_api.py:7341` | `except Exception: logger.warning` on forced WTM | + trace `Words Built/FAILED reason=sv3_forced_wtm_exception:<Type>` | No silent WTM swallow |
| `api/auto_dub_api.py:7394` | `except Exception: logger.warning` on SV3 | + trace `Meaning Pipeline/FAILED reason=sv3_exception:<Type>` | No silent SV3 swallow |
| `api/auto_dub_api.py:7410` (pre-fix) | `persist_to_task_info(...)` UnboundLocalError | Local import `_persist_wtm_to_task_info` + trace | **Root-cause fix** |
| `api/auto_dub_api.py:7444` | No guard when `seg_lines` empty after merge | `fail_stt_zero_segments()` → `ArchitectureViolation` + trace | Hard fail at Words Built boundary |
| `api/auto_dub_api.py:7630–7647` | `elif not seg_lines` only SKIPPED trace inside SV3 try | Explicit `semantic_v3_disabled` / `no_source_segments_from_stt` reasons | Documents skip decision-taker |
| `engines/semantic_v3/phase2.py:128` | No zero-input guard | `ArchitectureViolation` + `Meaning Pipeline/FAILED` | Phase2 cannot start with 0 ASR rows |

---

## 6. Legacy-path bypass audit

| Switch point | File:line | Condition | Trace logged |
|---|---|---|---|
| Semantic V3 gate | `api/auto_dub_api.py:7489` | `semantic_v3_enabled()` | `auto_dub_api.py:7294` debug + `_launch_trace_stage` |
| Legacy translate skip | `api/auto_dub_api.py:7579–7586` | `_native_te` sets `skip_translate=True` | `_launch_trace_agent translation called_by=native_translate` |
| Legacy adaptation | `api/auto_dub_api.py:~8100+` | Runs only if pipeline survives STT | N/A in failing run (STT crash) |
| Legacy scheduler/dub | `api/auto_dub_api.py` TTS/timing steps | Post-translate path | Deferred — out of scope per TZ |
| Explicit opt-out | `engines/semantic_v3/__init__.py:22–27` | `VM_SEMANTIC_V3=0` | `Meaning Pipeline/SKIPPED reason=semantic_v3_disabled_by_flag` |

**Failing run:** No legacy bypass taken — crash occurred before any downstream switch.

---

## 7. Feature-flag matrix

| Flag | Configured (`data/feature_flags.json`) | Effective (failing run evidence) | Gates new pipeline? | Gate line |
|---|---|---|---|---|
| `semantic_v3` | `enabled: true` (L165) | Never evaluated (STT crash first) | Yes — `run_semantic_v3_phase2` | `api/auto_dub_api.py:7489` |
| `word_timing` | `enabled: true` (L151) | Likely `false` at STT (`modes: pro,developer` only); forced `true` by SV3 WTM | Indirect — STT word timestamps + persist path | `api/auto_dub_api.py:7120, 7264, 7504` |
| `source_separation` | `enabled: true` (L80) | `fallback_used: true` (`л.json`) | No — enriches STT audio path only | `api/auto_dub_api.py:7047` |
| `dubbing` | `enabled: true` (L52) | Pipeline entered | Yes — whole AutoDub | `api/auto_dub_api.py:6588` |
| `translation` | `enabled: true` (L24) | Never reached | Native TE via SV3 when on | `engines/semantic_v3/__init__.py:36–39` |
| `VM_SEMANTIC_V3` env | unset → flag default | N/A (crash before gate) | Override for `semantic_v3_enabled()` | `engines/semantic_v3/__init__.py:23–27` |
| `VM_SEMANTIC_V3_NATIVE_TE` | default `1` | N/A | Native translation in Phase2 | `engines/semantic_v3/__init__.py:38` |

---

## 8. Post-fix runtime evidence

**Verification:** `python -m pytest tests/test_launch_decision_trace.py tests/test_meaning_first_pipeline.py tests/test_semantic_v3.py tests/test_semantic_v3_phase2.py -q` → **55 passed**

**Phase2 fixtures:** short+long (2 sentences, 2 locked); George Lucas golden (11 sentences, 11 locked, `regression_wall.passed=true`).

### Cited log lines (`debug-7e57dc.log`)

**(a) Decision Trace stage records**

```
{"runId":"meaning-first-launch","kind":"stage","stage":"Meaning Pipeline","status":"SUCCESS","reason":"phase2_entered",...}
{"runId":"meaning-first-launch","kind":"stage","stage":"Sentence Builder","status":"SUCCESS","reason":"semantic_core_ok",...}
{"runId":"meaning-first-launch","kind":"stage","stage":"Translation","status":"SUCCESS","reason":"native_translate_ok",...}
{"runId":"meaning-first-launch","kind":"stage","stage":"Variant Generator","status":"SUCCESS","reason":"meaning_fit_variants_scored",...}
{"runId":"meaning-first-launch","kind":"stage","stage":"Duration Predictor","status":"SUCCESS","reason":"phoneme_predictor_applied",...}
{"runId":"meaning-first-launch","kind":"stage","stage":"Meaning Fit","status":"SUCCESS","reason":"rewrite_and_regression_wall_ok",...}
{"runId":"meaning-first-launch","kind":"stage","stage":"Adaptation","status":"SUCCESS","reason":"adjacent_scene_revalidation_complete",...}
```

**(b) `lockedCount=0` before adaptation**

```
{"hypothesisId":"H4","message":"Translation lock state immediately after translation","data":{"sentenceCount":2,"lockedCount":0,"translatedCount":2}}
{"hypothesisId":"H2,H4","message":"Rewrite eligibility after decision policy","data":{"totalSentences":2,"rewritableCount":2,"lockedCount":0}}
```

**(c) Regression Wall passing**

```
{"hypothesisId":"AR-ET10","message":"Regression Wall verdict before LOCK","data":{"passed":true,"checksRun":8,"checksPassed":8,"lockCountBeforeAdaptation":0}}
```

**(d) AI agents — explicit decision-takers (no bare `not_called`)**

```
{"kind":"agent","agent":"semantic","status":"CALLED","called_by":"engines/semantic_v3/semantic_core.py:run_semantic_core"}
{"kind":"agent","agent":"translation","status":"CALLED","called_by":"engines/semantic_v3/native_translate.py:translate_sentences_native"}
{"kind":"agent","agent":"mix","status":"SKIPPED","skipped_reason":"mix_deferred_to_autodub_render_stage"}
```

**(e) George Lucas golden end-to-end**

```
{"data":{"asrInputCount":20,...}}  # Meaning Pipeline entered with 20 ASR rows
{"data":{"passed":true,"checksRun":8,"checksPassed":8,"slotCount":11,"lockCountBeforeAdaptation":0}}
{"data":{"sentenceCount":11,"lockedAfter":11,"lockOrderCorrect":true}}
```

---

## 9. What was NOT changed (TZ compliance)

- Translation engine logic (`engines/translation_pipeline.py`, legacy array TE)
- Dub Engine v2 functional logic (`engines/dub_engine_v2/**`)
- Scheduler functional logic
- TTS generation logic
- Meaning Fit engine logic (`engines/semantic_v3/meaning_fit_engine.py`)
- Audio optimization / timing fit logic

Only added: Decision Trace call sites, `fail_stt_zero_segments` guard, `persist_to_task_info` import fix.
