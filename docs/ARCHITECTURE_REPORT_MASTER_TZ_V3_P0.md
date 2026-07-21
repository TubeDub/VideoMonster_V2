# Architecture Report — MASTER TECHNICAL SPECIFICATION v3.0

**Phase:** P0 — Inventory (no implementation)  
**Date:** 2026-07-12  
**Workspace:** `VideoMonster_V2`  
**Diagnostic input:** `C:\Users\serhii\Desktop\т.json` (task `2ec2f545abdc4c358916fae74e1e91fd`, en→uk George Lucas)  
**Status:** COMPLETE — awaiting customer confirmation before P1  

---

## 1. Goal of P0

Build a factual map of imports, ownership, pipeline FSM, contracts, UUID/identity, and gaps vs TZ v3.0 **before any code changes**. Development of P1+ is forbidden until this report is accepted.

---

## 2. Target architecture (TZ)

```
Whisper → Translation Engine → Translation Validation → TRANSLATION LOCK
  → Dub Engine → Scheduler → AudioTimingOptimizer → TTS
  → Alignment → Merge → Studio → Export
```

**Rules after LOCK:** text immutable; audio/timing only.  
**Rules forever:** no symptom patches, no silent fix, no direct Segment field writes outside owner, module isolation, automatic architecture tests.

---

## 3. Module ownership map (as-is)

| Domain | Intended owner (TZ) | Current primary code | Owns today | Violations / notes |
|--------|---------------------|----------------------|------------|--------------------|
| Transcript | Whisper | `engines/stt_engine.py`, Whisper cache in `api/auto_dub_api.py` | Transcript | Orchestrator also owns flow |
| Text | Translation | `engines/translation_*.py`, `engines/dsal/`, `engines/ai_core/` | Text (pre-LOCK) | God-orchestrator in `auto_dub_api` |
| LOCK policy | Integrity | `engines/pipeline_integrity/translation_lock.py` | Immutability gates | Raises `TranslationLockError` (no `ArchitectureViolation` type yet) |
| Time | Scheduler | `engines/scheduler/` | Timing API | **Studio** (`api/studio_api.py`) still writes `start_ms`/`end_ms` directly |
| Audio fit | AudioTimingOptimizer | `engines/audio_timing_optimizer.py` | Tempo/trim via Scheduler | No dedicated FSM state `OPTIMIZED` |
| Audio synth | TTS | `engines/tts.py`, `engines/tts_engines/` | Audio files | `sanitize_tts_text` only (cleanup) |
| Mix | Merge | merge paths in pipeline / segment merger | Mix | — |
| UI | Studio | `api/studio_api.py`, `api/dub_studio_api.py` | UI + **timing writes** | Timing ownership leak |
| Policy | Integrity | `engines/pipeline_integrity/*` | Contracts/FSM/UUID | Solid core; plugins/golden stub |

**Parallel stack:** `engines/streamdub/` — second architecture; must not conflate with Freeze path.

---

## 4. Dependency / import edges (critical)

| Edge | TZ | Reality |
|------|----|---------|
| `engines/dub/` → Translation / LLM / AI Core | FAIL | **Clean** (static) |
| `engines/dubbing_engine/` → `translation_adapt` / `ai_core` | FAIL | **Clean** static; adaptation via **injected** `adapt_fn` (pre-LOCK by design) |
| `engines/scheduler/` → Translation / LLM | FAIL | **Clean** |
| `api/auto_dub_api.py` → Translation + LLM + Dub + TTS | Orchestrator OK | **God object** — couples everything |
| `engines/segment_timing_qa.py` → `translation_adapt` | Forbidden post-LOCK | **Legacy rewrite API still present** (no lock check) |
| `engines/closed_loop_timing.py` → rewrite | Only if unlocked | **Gated** by LOCK → marks overflow |
| TTS → semantic rewrite | Forbidden | Not found |

Architecture tests: `tests/test_dub_engine_architecture_p1.py`, `tests/test_dub_engine_import_lint_v2.py`, `engines/release_governance/architecture_audit.py`.

---

## 5. Pipeline FSM (P6 gap analysis)

**Implemented** (`engines/pipeline_integrity/pipeline_state.py`):

```
NEW → TRANSCRIBED → TRANSLATED → VALIDATED → LOCKED
  → TTS_READY → SCHEDULED → MERGED → HANDOFF → EXPORTED
```

**TZ v3.0 required:**

```
… → LOCKED → TTS_READY → OPTIMIZED → SCHEDULED → MERGED → HANDOFF → EXPORTED
```

| Gap | Severity |
|-----|----------|
| Missing state **`OPTIMIZED`** (AudioTimingOptimizer has no FSM stamp) | High |
| ADR-005 omits `HANDOFF` and `OPTIMIZED` | Doc drift |
| Rollback forbidden — **aligned** with TZ | OK |

---

## 6. Translation LOCK (P2) — maturity

| Check | Status |
|-------|--------|
| LOCK after Validation | Wired (`translation_validation` + `auto_dub_api`) |
| Locked text fields immutable via API/guards | **Solid** → `TranslationLockError` |
| Type name `ArchitectureViolation` | **Missing** (use existing integrity exceptions) |
| Silent fix after LOCK | **Forbidden in docs/code**; residual risk: `post_tts_validate_and_retry` if re-enabled |
| Production post-TTS path | Uses lock-aware `closed_loop_timing` | OK |

---

## 7. Contracts (P5)

- `stage_contracts.py` — per-stage mutation whitelist (`stt`…`studio_handoff`).
- `contract_versions.py` — all contracts at **version 1** (flat; no evolution yet).
- Named TZ contracts (`translation_contract`, `scheduler_contract`, …) exist as version stamps + stage whitelist, not separate versioned schema packages.

**Drift:** `slot_fit` whitelist vs arch test expectations for `start_ms` — enforcement inconsistency.

---

## 8. UUID / Audio Identity (P14)

- `uuid_chain.py`: `segment_uuid`, `translation_uuid`, `tts_uuid`, `audio_uuid`, `merge_uuid`.
- `audio_identity.py`: unique TTS basenames; handoff **repairs** duplicates instead of always aborting.
- TZ DoD “нет PIPELINE_AUDIO_IDENTITY” vs current **repair-then-continue** — soft compliance, not hard-fail governance.

---

## 9. pipeline_integrity maturity vs phases

| TZ phase | Maturity |
|----------|----------|
| P2 LOCK | Solid |
| P3 Immutable Segment whitelist | Solid (policy) |
| P4 Single Owner | Partial (Studio/conflict leaks) |
| P5 Contracts | Solid v1 / incomplete named schemas |
| P6 FSM | Solid minus `OPTIMIZED` |
| P7 Dub isolation | Package solid; injection + orchestrator soft |
| P8 Scheduler sole time owner | API solid; Studio bypass |
| P9 AudioTimingOptimizer levels | Partial (levels exist; not full TZ ladder) |
| P10/P11 Overflow/Underflow managers | Partial (flags/marks; not first-class FSM states) |
| P12 Smart Adaptation (multi-variant pre-LOCK) | Partial / DSAL + engines; not full variant selector |
| P13 Quality Evaluator | Partial (scores scattered) |
| P14 UUID | Solid + soft identity repair |
| P15 Runtime Validator | Solid |
| P16 Recovery | Partial–solid |
| P17 Diagnostics / OpenDDF | Solid (observe) |
| P18 Benchmark scale | Not at 1e3–1e5 gate |
| P19 Golden | Scaffold / thin |
| P20 Architecture tests | Real but incomplete vs full TZ matrix |
| P21 Performance budget | Not automated as hard gate |
| P22 Plugin SDK | Stub registry |
| P23 Release governance | Present; tighten for DoD |
| P24 Live defects | See §11 |

---

## 10. P1 freeze implication (for next phase)

**P1 — Translation Freeze:** Translation Engine is declared stable. Until a separate decision/PR:

**Do not change:** prompts, rewrite, semantic, grammar, glossary, validation logic, adaptation (including DSAL unless explicitly out of freeze scope — **confirm with customer**).

All Translation changes require a dedicated PR after freeze declaration.

---

## 11. P24 live problems (from code + `т.json`)

Diagnostic task `2ec2f545…` (George Lucas en→uk):

- Segment rows show `adaptation_status: ADAPTATION NOT EXECUTED` with `adaptation_executed: false` while pipeline may have run DSAL/QA elsewhere — **false diagnostic messaging** (root: flag propagation / OpenDDF summary, not only UI).
- `final_tts_duration_ms: 0` / `actual_duration_ms: 0` on early segments in dump — TTS lifecycle / report incompleteness risk.
- Overlap/overflow fields present in schema (`overlap_info`, `slot_overflow`) — need full-run aggregate for DoD “нет overlap”.

**Architectural root causes (not symptoms):**

1. God-orchestrator (`auto_dub_api`) bypasses Single Owner.
2. Studio timing writes outside Scheduler.
3. Legacy post-TTS rewrite API without LOCK gate.
4. Missing `OPTIMIZED` FSM state.
5. Identity repair hides identity contract violations.
6. Dual pipeline (Freeze vs StreamDub) without single contract spine.
7. Incomplete Overflow/Underflow as first-class pipeline states with Studio recovery plans.

---

## 12. Recommended phase order (strict, per TZ)

| Phase | Action | Gate |
|-------|--------|------|
| **P0** | This report | **YOU ARE HERE** — customer confirm |
| **P1** | Declare Translation Freeze + process only | Report + no TE code churn |
| **P2–P3** | Harden LOCK + Immutable whitelist + `ArchitectureViolation` alias | Arch tests |
| **P4–P5** | Close Studio/conflict ownership; versioned contract packages | Arch tests |
| **P6** | Add `OPTIMIZED`; sync ADR-005 | FSM tests |
| **P7** | Dub isolation: remove soft injection leaks post-LOCK | Import lint |
| **P8–P9** | Scheduler-only timing; ATO level ladder | Unit + arch |
| **P10–P11** | Overflow/Underflow managers (no text change) | Studio UX + tests |
| **P12–P13** | Multi-variant pre-LOCK + Quality Evaluator | Golden |
| **P14–P17** | Hard identity, validator, recovery, diagnostics | No soft repair as default |
| **P18–P19** | Benchmark + Golden | No regress |
| **P20–P23** | Arch tests matrix, perf budget, plugins, release gate | CI red = no ship |
| **P24** | Close remaining live defects using roots above | Full DoD |

---

## 13. Explicit non-goals for post-P0 work until confirmed

- No new DSAL/LLM symptom patches in Translation while P1 Freeze is active (unless customer carves an exception).
- No “quick if” in Merge/TTS to hide overlap.
- No silent text rewrite after LOCK.

---

## 14. Customer confirmation checklist

Please confirm:

1. [ ] P0 Architecture Report accepted  
2. [ ] Proceed to **P1 Translation Freeze** only  
3. [ ] DSAL / duration adaptation: **inside Freeze** (no changes) / **exception PR allowed** (choose one)  
4. [ ] StreamDub: **out of scope** for Freeze path / **must converge** (choose one)  
5. [ ] Audio identity: prefer **hard-fail** on duplicate (TZ DoD) vs current **repair** (choose one for P14)

---

## 15. References

- Spec: user Master TZ v3.0 (this chat)  
- Inventory agent: detailed package table in session  
- Existing docs: `docs/adr/ADR-005-state-machine.md`, `ARCHITECTURE_AUDIT_REPORT.md`, `engines/pipeline_integrity/`  
- Diagnostic: `C:\Users\serhii\Desktop\т.json`
