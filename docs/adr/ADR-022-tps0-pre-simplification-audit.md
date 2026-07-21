# ADR-022 — TPS0 Pre-Simplification Audit (Translation Fast Path v2)

**Status:** Proposed — awaiting customer sign-off  
**Date:** 2026-07-17  
**Stage:** TPS0 only (no behavior change)  
**Related TZ:** Translation Pipeline Simplification (TPS) / Translation Fast Path v2 v1.0

## Context

Audit of the AutoDub translation → TTS path confirmed the customer thesis:

> Problem is not the base MT quality alone — **too many modules rewrite the same text**.

This ADR is the mandatory **Pre-Simplification Audit** (TZ Part 5).  
**No modules are deleted in TPS0.** Behavior is unchanged until TPS1+ after sign-off.

## Decision (TPS0)

1. Publish a full registry of writers / validators / routers / observers.
2. Name confirmed duplicate writers (especially timing-adapt).
3. Propose TPS roles (`always` / `on-fail` / `judge-only` / `deprecated-candidate` / `keep-as-capability`).
4. Freeze Storage Manager (out of scope).
5. Do **not** start routing simplification until this ADR is accepted.

---

## 1. Current call order (manifest / agent path)

| # | Stage | Primary location | Notes |
|---|--------|------------------|-------|
| 0 | Semantic V3 / Meaning Fit (optional) | `engines/semantic_v3/phase2.py` | May prefill / skip translate |
| 1 | MT (Translation Agent) | `engines/ai_core/translation_agent/` | Raw `translated_text` |
| 2 | Translation Review snapshot | `engines/translation_review.py` | Often **before** later rewriters |
| 3 | Semantic → Timing → Grammar → Quality → Reviewer | `AICoreOrchestrator` via `api/auto_dub_api.py` | Multiple writers |
| 4 | DSAL + pre-lock polish + LOCK stamp | `engines/dsal/`, `translation_validation.py` | Another timing/meaning writer |
| 5 | Resolve Final texts | `tts_text_path` / `translation_validation` | Competing field priority |
| 6 | TAT / AI Adaptation | `timing_aware_translation` / `ai_adaptation_engine` | **Skipped** if `translation_agent_path` |
| 7 | DubbingEngine (adapt + punct + stress + phonetics) | `engines/dubbing_engine/engine.py` | Can rewrite again |
| 8 | Sentence integrity | `sentence_integrity.py` | Repair writer |
| 9 | Adaptation / language gates | `ai_adaptation_engine`, `pipeline_language_gate` | Validators (+ recovery writer) |
| 10 | **TQE** | `engines/tqe/` | Gate + **can rewrite on retry** |
| 11 | TTS groups / generate | `translation_naturalizer.build_tts_groups`, `tts.py` | Cosmetics: stress/SSML |
| 12 | Post-TTS slot-fit / overflow adapt | `timing_fit`, `translation_adapt` | Text rewrite if unlocked |

Legacy (no-manifest) path: Marian/Argos → `translation_naturalizer` / `naturalizer_v2` → then similar gates.

---

## 2. Module registry

Legend:
- **W** = WRITER (may change words)
- **V** = VALIDATOR (check only)
- **R** = ROUTER (decide who runs)
- **O** = OBSERVER (metrics/logs)

### 2.1 MT / entry

| Module | Files | Cat | Text? | LLM? | TPS role proposal | Duplicates |
|--------|-------|-----|-------|------|-------------------|------------|
| TranslationAgent | `engines/ai_core/translation_agent/` | W | YES | NO | **always** (MT owner) | UniversalTranslationPipeline, semantic_v3 native_te |
| ArgosEngine | `engines/mt/argos_engine.py` | W | YES | NO | keep-as-capability | Marian, NLLB, Deep |
| Marian / NLLB / Deep | `engines/mt/*` | W | YES | NO | keep-as-capability | Argos |
| TranslationRouter | `engines/translation_router.py` | R | via engines | NO | keep-as-capability | mt.registry |
| UniversalTranslationPipeline | `engines/translation_pipeline.py` | W+R | YES | YES | **deprecated-candidate** as primary (keep as capability/fallback) | TranslationAgent |
| Event Bus translation | `core/event_pipeline.py` | R | YES | YES | keep-as-capability | UniversalTranslationPipeline |
| semantic_v3 native_translate | `engines/semantic_v3/native_translate.py` | W | YES | NO | keep-as-capability / on-fail | TranslationAgent |

### 2.2 Naturalization / polish

| Module | Files | Cat | Text? | LLM? | TPS role proposal | Duplicates |
|--------|-------|-----|-------|------|-------------------|------------|
| translation_naturalizer | `engines/translation_naturalizer.py` | W | YES | YES | **always rule-first** (Fast Path); LLM path → on-fail | naturalizer_v2, SemanticAgent |
| naturalizer_v2 | `engines/naturalizer_v2/` | W | YES | YES | keep-as-capability (entity mask) | translation_naturalizer |
| DSAL pre_lock_polish | `engines/dsal/pre_lock_polish.py` | W | YES | NO | on-fail / cosmetics | text_preparation, Grammar punct |
| text_preparation | `engines/text_preparation.py` | W | YES (abbr) | NO | keep-as-capability (post-approve cosmetics only) | pre_lock_polish |

### 2.3 Semantic / meaning

| Module | Files | Cat | Text? | LLM? | TPS role proposal | Duplicates |
|--------|-------|-----|-------|------|-------------------|------------|
| SemanticAgent | `engines/ai_core/semantic_agent/` | W | YES | YES | **on-fail** (Single Owner semantic rewrite) | naturalizer, meaning_fit, ai_adaptation |
| semantic_meaning | `engines/semantic_meaning.py` | V (+compact) | compact YES | NO | always (library validator) | TQE MeaningReviewer |
| Meaning Fit Engine | `engines/semantic_v3/meaning_fit_engine.py` | W | YES | YES | **Single Owner duration-text** after APPROVED / yellow-red only | TimingAgent, DSAL, TAT |
| meaning_preservation | `engines/semantic_v3/meaning_preservation.py` | V | NO | NO | always | semantic_meaning |
| semantic_adaptation (legacy) | `engines/semantic_adaptation.py` | W/O | YES | YES | deprecated-candidate | TimingAgent, DSAL |
| semantic_translation / optimizer | `engines/semantic_translation.py`, `semantic_optimizer.py` | W | YES | YES | deprecated-candidate | SemanticAgent |
| DirectorAgent | `engines/ai_core/director_agent/` | O/R | NO | YES | keep-as-capability | — |

### 2.4 Grammar

| Module | Files | Cat | Text? | LLM? | TPS role proposal | Duplicates |
|--------|-------|-----|-------|------|-------------------|------------|
| GrammarAgent | `engines/ai_core/grammar_agent/` | W | YES | YES | **on-fail** (Single Owner grammar rewrite) | naturalizer, TQE Grammar, sentence_integrity |
| TQE GrammarReviewer | `engines/tqe/reviewers/grammar.py` | V (+retry W) | on-fail | on-fail | judge / on-fail | GrammarAgent |

### 2.5 Timing / duration (CRITICAL DUPLICATE ZONE)

| Module | Files | Cat | Text? | LLM? | TPS role proposal | Duplicates |
|--------|-------|-----|-------|------|-------------------|------------|
| TimingAgent | `engines/ai_core/timing_agent/` | W | YES | YES | **candidate Single Owner** OR fold into Meaning Fit | TAT, DSAL, DubEngine adapt, Meaning Fit |
| timing_aware_translation | `engines/timing_aware_translation.py` | W | YES | YES | deprecated-candidate when agent path always on | TimingAgent |
| ai_adaptation_engine | `engines/ai_adaptation_engine.py` | W+V | YES | YES | on-fail / merge into owner | TimingAgent, TAT |
| DSAL core | `engines/dsal/core.py` | W | YES | optional | on-fail under Timing/MeaningFit owner | TimingAgent |
| DSAL lock / clause / block_merge | `engines/dsal/lock_gate.py` etc. | W/V | YES | NO | keep-as-capability | TimingAgent |
| DubbingEngine `_stage_adapt` | `engines/dubbing_engine/engine.py` | W | YES | YES | **deprecated-candidate for text adapt**; keep stress/phonetics | TimingAgent, DSAL |
| translation_adapt / post-TTS slot_fit | `translation_adapt.py`, `timing_fit.py` | W | YES | YES | audio-first always; text rewrite on-fail only | TimingAgent |
| TQE TimingReviewer | `engines/tqe/reviewers/timing.py` | V | on-fail | on-fail | judge / on-fail | TimingAgent |

### 2.6 Gates / TQE / integrity

| Module | Files | Cat | Text? | LLM? | TPS role proposal | Duplicates |
|--------|-------|-----|-------|------|-------------------|------------|
| **TQE** | `engines/tqe/` | R+V (+W on retry) | on-fail | on-fail | **always — central router (TPS1+)** | QualityAgent, ReviewerAgent |
| QualityAgent | `engines/ai_core/quality_agent/` | V+R | fallback YES | NO | judge-only / merge into TQE | TQE |
| ReviewerAgent | `engines/ai_core/reviewer_agent/` | R+V | via re-route | YES | on-fail / fold into TQE Judge | TQE, QualityAgent |
| sentence_integrity | `engines/sentence_integrity.py` | V+W repair | YES | NO | on-fail under Grammar owner | TQE sentence, GrammarAgent |
| translation_validation | `engines/translation_validation.py` | V+W stamp/lock | YES | via DSAL | always (ownership API) | — |
| pipeline_language_gate | `engines/pipeline_language_gate.py` | V | NO | NO | always | TranslationAgent lang check |
| translation_review | `engines/translation_review.py` | O (+UI W) | UI YES | NO | Manual Review path | — |
| translation_quality / logs / diagnostics | `translation_quality*.py`, `translation_trace.py`, … | O/V | NO | NO | keep-as-capability | — |

### 2.7 Post-approve cosmetics (allowed after Single Approved Text)

| Module | Files | Cat | Text? | LLM? | TPS role proposal |
|--------|-------|-----|-------|------|-------------------|
| stress_marks | `engines/stress_marks.py` | W prosody | marks only | NO | keep-as-capability |
| DubbingEngine phonetics | `engines/dubbing_engine/phonetics.py` | W | YES (pronunciation forms) | NO | keep-as-capability (careful: can change tokens) |
| VoicePreparationAgent | `engines/ai_core/voice_preparation_agent/` | W SSML | YES | NO | keep-as-capability |

---

## 3. Confirmed duplicate writers (must resolve in TPS2)

| Concern | Competing writers today | TPS target owner |
|---------|-------------------------|------------------|
| Raw MT | TranslationAgent, UniversalTranslationPipeline, native_te, TQE Argos repair | **MT Engine / TranslationAgent** |
| Naturalization | translation_naturalizer, naturalizer_v2, SemanticAgent, GrammarAgent | **Naturalizer (rule-first always)** |
| Semantic rewrite | SemanticAgent, meaning_fit, ai_adaptation, TQE meaning retry, Reviewer re-route | **Semantic Rewrite Owner (on-fail)** |
| Grammar rewrite | GrammarAgent, TQE grammar, sentence_integrity, DSAL clause glue | **Grammar Rewrite Owner (on-fail)** |
| Timing / duration text | TimingAgent, TAT, ai_adaptation, DSAL, DubEngine adapt, Meaning Fit, post-TTS adapt | **ONE Timing/MeaningFit Owner** |
| Final approve | QualityAgent, ReviewerAgent, TQE, auto review resume | **TQE only** |

### Live double timing-adapt (agent happy path)

```
TimingAgent → DSAL@LOCK → DubbingEngine._stage_adapt → (TQE timing retry) → (post-TTS text adapt if unlocked)
```

TAT is skipped when `translation_agent_path` is set, but **three writers remain**.

---

## 4. Text field divergence today (why Review ≠ TTS)

| Field | Typical owner | Problem |
|-------|---------------|---------|
| `translated_text` / Raw MT | MT | Intentionally frozen — good |
| `semantic_text` | SemanticAgent | May override Final via authority preference |
| `timing_text` | TimingAgent | Not always stamped into audit Final |
| `grammar_text` | GrammarAgent | Becomes TTS candidate |
| `final_text` / `voice_input` | stamp / sync | Later stages mutate without syncing all fields |
| `tts_text` | TTS / DubEngine | Stress stripped/readded; SSML leakage historically |
| Review snapshot | Built early (~post-MT) | Orchestrator + DSAL + DubEngine + TQE run **after** auto-approve |

**Conclusion for TPS3:** introduce `approved_text` + lock; Review/TTS/Scheduler must read **only** that field.

---

## 5. Deprecate candidates (NOT deleted in TPS0)

A module may become `deprecated-candidate` only with coverage proof (TZ Part 5). Proposed:

| Candidate | Covered by | Required proof before remove |
|-----------|------------|------------------------------|
| UniversalTranslationPipeline as **primary** | TranslationAgent + Naturalizer rule-first | Golden parity tests agent path |
| translation_naturalizer as **primary LLM writer** | Naturalizer rule-first + Semantic on-fail | Fast Path metrics + golden |
| timing_aware_translation (when agent always on) | Timing/MeaningFit Owner | `no_dual_timing_adapt` arch test |
| DubbingEngine text **adapt** stage | Timing/MeaningFit Owner | Keep stress/phonetics; adapt off on happy path |
| semantic_translation / semantic_optimizer | SemanticAgent | No callers in AutoDub happy path |
| legacy semantic_adaptation duration rewrite | Timing/MeaningFit Owner | Metrics + tests |

**No deletion without:** `«функциональность X полностью покрыта модулем Y + тесты Z»` in a later phase report.

---

## 6. Target contour (for TPS4 — not implemented in TPS0)

```
Whisper → MT → Naturalizer(rule-first) → Fast QA
   PASS → TQE → APPROVED → (Meaning Fit if yellow/red) → TTS
   FAIL → Meaning/Grammar Retry (exactly 1) → TQE
            PASS → APPROVED → TTS
            FAIL → LLM Judge → TQE
                     PASS → APPROVED → TTS
                     FAIL → Manual Review
```

TQE becomes the **only** PASS/FAIL router.  
Word-count EN vs UK must **not** be a hard FAIL (clause/entity/meaning instead).

---

## 7. TPS0 acceptance checklist

- [x] Full module registry published (this ADR)
- [x] Writers / validators / routers / observers classified
- [x] Confirmed duplicate writers listed (timing triple path)
- [x] Field divergence Review ≠ TTS documented
- [x] Deprecate candidates listed **with** required coverage proof (no silent deletes)
- [ ] **Customer sign-off** ← blocking for TPS1
- [ ] TPS1 not started until sign-off

## 8. Explicit non-goals of TPS0

- No routing change
- No Single Approved Text implementation yet
- No Performance Dashboard yet
- No dual-writer architecture tests yet
- Storage Manager remains feature-frozen

## 9. Next phase (only after sign-off)

**TPS1 — TQE + Fast QA skeleton**
- TQE as router/gate with statuses: `PASS | FAIL_RETRY_MEANING_GRAMMAR | FAIL_LLM_JUDGE | FAIL_MANUAL_REVIEW`
- Fast QA without EN/UK word-count hard fail
- Unit tests: every segment gets `tqe_status`

---

## Sign-off

| Role | Name | Date | Decision |
|------|------|------|----------|
| Customer | | | ☐ Accept TPS0 / ☐ Request changes |
| Engineer | Cursor Agent | 2026-07-17 | Audit delivered |

**Comment / requested changes:**

_______________________________________________
