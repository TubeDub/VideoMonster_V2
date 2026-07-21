# P2 DSAL v4.0 Report — SSML + LOCK Gate + Audio Fit ±5%

**Date:** 2026-07-12  
**Scope:** TZ v4.0 P2 (MVP complete with P0+P1)  
**Golden:** George Lucas en→uk

---

## Verdict

P2 delivered: SSML breaks capped at **350ms** with no mid-name/`Jr.` sentence breaks; pre-LOCK polish for Fiat/USC/name periods; LOCK gate requires duration_match ≥ 85 + clause ≥ 0.85 + entity; post-LOCK audio tempo **0.95–1.05** only.

---

## What shipped

### SSML (`prosody.py` + `config.MAX_BREAK_MS=350`)
- All breaks clamped to ≤350ms
- Skip sentence-break after `Jr./Sr./Mr./Dr.` and false period after `Джордж-молодший.`

### Pre-LOCK polish (`engines/dsal/pre_lock_polish.py`)
- `Фіат,.` → clean punct
- Duplicate «Південної Каліфорнії» collapsed
- Mid-sentence `Джордж-молодший.` removed
- Wired before LOCK gate in `apply_translation_lock_after_validation`

### LOCK gate (`engines/dsal/lock_gate.py`)
- Pass: `duration_match_score ≥ 85` (or band=green), `clause_coverage ≥ 0.85`, entity OK
- Fail: segment `needs_studio=True`, project `translation_lock_deferred`
- Passing segs still lock; `VM_FORCE_TRANSLATION_LOCK=1` bypass for CI

### Audio fit ±5%
- `audio_timing_optimizer`: TEMPO 0.95–1.05
- `timing_fit._ATEMPO_MIN`: 0.95
- `conflict_resolver.SAFE_ATEMPO_MIN`: 0.95
- Text rewrite after LOCK remains forbidden

---

## Tests

`tests/test_dsal_p2.py` + P0/P1 → **20 passed**

---

## George Lucas checklist (code-level)

| Item | Status |
|------|--------|
| #4 no `Фіат,.` | ✅ polish |
| #5/#6 clauses | ✅ P1 |
| #6 underflow | ✅ P0/P1 |
| #11 no mid-name break | ✅ SSML + polish |
| #13 one «Південної Каліфорнії» | ✅ polish |
| LOCK after DSAL gate | ✅ |
| Audio ±5% after LOCK | ✅ |

Still needs a **live re-run** of task `0c5ddd…` to confirm fitted_file / overlap=0 on real audio.

---

## MVP status

| Phase | Status |
|-------|--------|
| P0 rule-based DSAL | ✅ |
| P1 clause + block merge + Golden | ✅ |
| P2 SSML + LOCK + audio ±5% | ✅ |
| P3 LLM-enhanced DSAL | optional |
| P4 Studio editorial | optional |
| P5 Benchmark | optional |

---

## Sign-off

MVP P0–P2 complete. Next optional: live George Lucas re-dub verification, then P3/P4 if needed.
