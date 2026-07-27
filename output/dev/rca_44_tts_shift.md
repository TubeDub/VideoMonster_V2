# RCA: Run 44 — TTS Text Shift / Phrase-Loop / Field Mismatch

**Task:** `4ba7e70fe9b94ca183ea47d83a1f9551` · **Target:** uk · **Segments:** 18  
**Sources:** `44.zip` (passive OpenDDF archive), `44.json` (developer export)  
**Report stage:** `ensure_archive` · **QA:** `qa_ok=false`, **58 issues** · **Exception:** none (`stacktrace.txt`: empty)

---

## Executive Summary

This run failed QA primarily because **spoken TTS text diverged from canonical segment text** on 3 segments (7, 8, 13), driven by (a) **Argos phrase loops** left in `tts_text`, (b) **unsplit MT blob bleed** across segments 6→7, and (c) **post-heal text updates without mandatory re-TTS**. No segment exhibits classic *truncation* (`final_tts_text` as strict prefix of `translated_text`); instead, **final text is longer than translated** where corruption occurs. All 18 segments skipped text adaptation (`FitsNoChange`, `llm_available=false`), forcing audio-only overflow/underflow handling.

---

## report.json — Stage / Exception

| Field | Value |
|-------|-------|
| `stage` | `ensure_archive` |
| `developer.stage` | `ensure_archive` |
| `developer.qa_ok` | `false` |
| `developer.issue_count` | `58` |
| `exception` | `{}` (empty) |
| `stacktrace.txt` | `(no stacktrace captured)` |

Pipeline did **not crash**; failure is QA/diagnostic, not an unhandled exception.

---

## Per-Segment Metrics (0-indexed / 1-indexed)

Lengths = Unicode char count. **Trunc** = `final_tts_text` strict prefix of `translated_text`. **Bleed** = neighbor/shift (see notes). **PL** = phrase loop detected in `final_tts_text`.

| idx | 1-based | orig | tr | adapt | pre | final | raw | Trunc | Bleed | PL | overflow_ms |
|-----|---------|------|----|-------|-----|-------|-----|-------|-------|----|-------------|
| 0 | 1 | 90 | 73 | 73 | 73 | 73 | 104 | — | — | — | 617 |
| 1 | 2 | 109 | 85 | 85 | 85 | 85 | 92 | — | — | — | 161 |
| 2 | 3 | 151 | 85 | 85 | 85 | 85 | 151 | — | — | — | 0 |
| 3 | 4 | 109 | 24 | 24 | 24 | 24 | 119 | — | — | — | 0 |
| 4 | 5 | 120 | 68 | 68 | 68 | 68 | 126 | — | — | — | 0 |
| 5 | 6 | 102 | **10** | 117 | **10** | **117** | 117 | — | — | — | 1379 |
| 6 | 7 | 93 | 98 | 98 | 98 | 98 | **430** | — | — | — | 1274 |
| 7 | 8 | 375 | 146 | **239** | 146 | **239** | **430** | — | **seg6 prefix in final/spoken** | — | 0 |
| 8 | 9 | 242 | 170 | 170 | 170 | 170 | 211 | — | — | **yes** | 0 |
| 9 | 10 | 148 | 97 | 97 | 97 | 97 | 132 | — | **wrong narrative**¹ | — | 0 |
| 10 | 11 | 151 | 51 | 51 | 51 | 51 | 157 | — | — | — | 0 |
| 11 | 12 | 74 | 76 | 76 | 76 | 76 | 92 | — | — | — | 12 |
| 12 | 13 | 94 | 103 | 103 | 103 | 103 | 76 | — | — | — | 0 |
| 13 | 14 | 193 | 388 | 388 | 388 | 388 | 196 | — | — | **yes** | 0 |
| 14 | 15 | 535 | 38 | 38 | 38 | 38 | 547 | — | — | — | 0 |
| 15 | 16 | 224 | 56 | 56 | 56 | 56 | 223 | — | — | — | 0 |
| 16 | 17 | 177 | 63 | 63 | 63 | 63 | 176 | — | — | — | 0 |
| 17 | 18 | 88 | 55 | 55 | 55 | 55 | 63 | — | — | — | 0 |

¹ Seg 9 `translated_text` describes near-death epiphany; `original_text` is racetrack/finish-line scene — semantic mis-assignment from timing compression, not neighbor exact-match.

### Field consistency flags

| Check | Segments |
|-------|----------|
| `final_tts_text` ≠ `pre_tts_text` | **5, 7** |
| `final_tts_text` longer than `translated_text` (+5 chars) | **5, 7** |
| `snapshot_after.text` ≠ `final_tts_text` | **7, 8, 13** |
| `snapshot_after.text` ≠ `pre_tts_text` | **5, 8, 13** |
| Phrase loop in `final_tts_text` | **8, 13** |

### adaptation_reasons / warnings / quality_reasons

- **`adaptation_reasons`:** empty on all 18 segments  
- **`warnings`:** empty on all 18 segments (segment_diagnostics)  
- **`quality_reasons`:** `null` on all 18 segments  
- **`adaptation_status`:** `ADAPTATION NOT EXECUTED` × 18  
- **`adaptation_skip_reason`:** `FitsNoChange` × 18  
- **`algorithm_reason`:** predominantly `AudioStrategyNoTextRewrite`

### final_dub_qa.json issue breakdown

| code | count |
|------|-------|
| `meaning_truncated_tail` | 15 |
| `split_sentence` | 14 |
| `long_pause` | 9 |
| `preserved_token` | 6 |
| `duration_overflow` | 4 |
| `overlap_with_next` | 4 |
| **`text_tts_mismatch`** | **3** (seg 7, 8, 13) |

---

## snapshot_after — text / tts_text / plain_text

`snapshot_after.json` exposes only **`text`** (no `tts_text` or `plain_text` keys). Comparison uses `text` vs segment diagnostics fields and QA `spoken`/`canonical` pairs.

| idx | snapshot `text` | diag `final_tts_text` | diag `pre_tts_text` | QA spoken (audit `tts_text`) |
|-----|-----------------|----------------------|---------------------|------------------------------|
| 5 | 117 (full) | 117 | 10 | — |
| 7 | 146 (split) | 239 (seg6+seg7) | 146 | **239 (bleed)** |
| 8 | 86 (healed) | 170 (loop) | 170 (loop) | **170 (loop)** |
| 13 | 318 (healed) | 388 (loop) | 388 (loop) | **388 (loop)** |

**Pattern:** For 7/8/13, **`text` was corrected in snapshot but audio/`tts_text` still reflects corrupt spoken input.** Seg 5 inverted: `translated_text`=10 but snapshot/TTS use full 117-char sentence.

---

## 44.json vs 44.zip divergence

`44.json` is a **later/alternate export** (shows `adaptation_executed=true`, pause_optimization success on seg 0). Zip `segment_diagnostics.json` captures **passive archive at `ensure_archive`** with all adaptation skipped. Treat zip as ground truth for this RCA; json44 useful for confirming DSAL/audio-fit ran in an earlier pass.

---

## Ranked Root Causes

### 1. Phrase loops reach TTS before heal/re-TTS (HIGH — seg 8, 13)

**Evidence:** `final_tts_text` and audit `tts_text` contain `у той момент` ×7; `final_dub_qa` `text_tts_mismatch` with loop in `spoken`, healed text in `canonical`. Snapshot `text` is deflated; MP3 still looped.

**Guess:** `engines/mt/argos_engine.py` (`has_phrase_loop` gate) → loop not rejected or bypassed; `engines/pipeline_language_gate.py:heal_phrase_loops_in_segments` → text healed but `api/auto_dub_api.py` re-TTS loop (lines ~12693–12750) skipped/failed silently; `engines/segment_timing_qa.py:detect_text_tts_mismatch` catches residual.

### 2. Unsplit MT blob → cross-segment bleed in spoken audio (HIGH — seg 6–7)

**Evidence:** Seg 6 & 7 share identical 430-char `raw_translation`. Seg 7 `final_tts_text` / QA `spoken` prepends seg 6 dinner sentence; `pre_tts_text` and snapshot `text` are correctly split (146 chars). Seg 7 `original_text` itself is corrupted (duplicated English clause), hinting resegmentation failure.

**Guess:** `engines/sentence_integrity.py` / timing-aware split → `raw_translation` not re-sliced per slot; `engines/segment_timing_qa.py` (`text_after_adaptation` from `adapt_trace.text_after`) records merged blob while TTS path uses partial `pre_tts_text` inconsistently.

### 3. Post-heal text ↔ audio desync (HIGH — seg 7, 8, 13)

**Evidence:** 3× `text_tts_mismatch`; snapshot `text` ≠ audit `tts_text`. Identity/heal updated display fields without synchronizing spoken artifact.

**Guess:** `engines/pipeline_integrity/guards.py` (IdentityGuard) or `heal_phrase_loops_in_segments` updates `text`/`plain_text` but `tts_text` + `file` stale; `api/auto_dub_api.py:_regen_segment_tts_simple` not invoked or fails without failing pipeline.

### 4. FitsNoChange blocks all text adaptation despite timing delta (MEDIUM — all 18)

**Evidence:** `need_adaptation=true` everywhere timing delta >500ms; `decision_engine=SKIPPED(FitsNoChange)`; `llm_available=false`; 15× underflow + 4× overflow handled as `AudioStrategyNoTextRewrite` / `dsal_exhausted_audio_fit`.

**Guess:** `engines/ai_adaptation_engine.py` (marks `fits_no_change` when LLM off) → `engines/dub_engine_v2/adaptation_decision.py:SKIP_FITS_NO_CHANGE` → `engines/dub_engine_v2/overflow_strategy.py` audio-only path; no text rewrite despite `duration_overflow:no_rewrite`.

### 5. translated_text truncated vs raw/full TTS (MEDIUM — seg 0–4, 9–17)

**Evidence:** 15× `meaning_truncated_tail`; typical pattern `raw` 2–10× longer than `translated_text` (e.g. seg 0: raw 104 → tr 73; seg 14: raw 547 → tr 38). Not `final_tts` prefix truncation — **`translated_text` itself is tail-dropped** before TTS.

**Guess:** Timing-aware compression / slot-fit shorten path in closed-loop or semantic adapter; `engines/closed_loop_timing.py` + DSAL rewrite chain.

### 6. Seg 5 field schizophrenia (MEDIUM)

**Evidence:** `translated_text`=`"Наприклад."` (10) but `pre_tts_text` also 10 while `final_tts_text`/snapshot/TTS = 117 (full question). `text_after_adaptation` = full sentence.

**Guess:** `translated_text` sourced from `audit.final_text` (truncated) while TTS pulled from `raw_translation` / `text` field — `engines/segment_timing_qa.py:2110–2170` dual sourcing.

### 7. Diagnostics mis-report final vs spoken (LOW — observability)

**Evidence:** Seg 7 `pre_tts_text` matches snapshot; `final_tts_text` matches corrupt `text_after_adaptation`/audit path. Seg 8/13 diagnostics show loop text though snapshot healed.

**Guess:** `engines/segment_timing_qa.py:build_segment_diagnostics_rows` — `final_tts` resolved from `audit.tts_text` (spoken) but snapshot built from post-heal `seg.text`; fields not reconciled at archive time.

---

## Top 5 Fix Targets

| Priority | Target | Action |
|----------|--------|--------|
| **P0** | `api/auto_dub_api.py` — phrase-loop heal + re-TTS block (~12693) | Fail closed if `tts_text` ≠ healed `text` after heal; mandatory re-TTS + refit; propagate failure to QA |
| **P0** | `engines/pipeline_language_gate.py:heal_phrase_loops_in_segments` | Run **before first TTS**, not after; sync `tts_text`, `plain_text`, `text`, `translated_text` atomically |
| **P0** | `engines/sentence_integrity.py` + MT split alignment | When `raw_translation` spans multiple slots, slice per-segment **before** TTS; reject/bleed if spoken text ⊃ neighbor translation |
| **P1** | `engines/mt/argos_engine.py` | Hard-reject or auto-deflate `phrase_loop` at MT boundary (already has hook ~130); never pass loop text to TTS queue |
| **P1** | `engines/segment_timing_qa.py:build_segment_diagnostics_rows` + `detect_text_tts_mismatch` | Single source of truth: `final_tts_text` = actual spoken (`tts_text`); block `ensure_archive` on mismatch; fix `translated_text` vs `raw` dual-source for seg 5-class bugs |

---

## What This Is NOT

- **Not classic truncation:** 0/18 segments have `final_tts_text` as strict prefix of `translated_text`.
- **Not exact neighbor swap:** no segment's `final_tts_text` equals another segment's full `translated_text`; bleed is **prefix merge** (7) and **MT loop inflation** (8, 13).
- **Not a runtime crash:** stage completed to archive with QA failure.

---

*Generated from automated analysis of `44.zip` + `44.json` · run_id `4ba7e70fe9b94ca183ea47d83a1f9551`*
