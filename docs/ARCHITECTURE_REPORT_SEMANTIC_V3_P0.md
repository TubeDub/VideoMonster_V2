# Architecture Report — VideoMonster V3 Semantic Engine

**Spec:** Semantic Translation Engine + Intelligent Dub Engine v3.0  
**Phase:** P0 — Whisper stops owning segments (inventory only)  
**Date:** 2026-07-12  
**Status:** COMPLETE — awaiting customer acceptance before P1  

**Naming note:** This is **not** Master Freeze TZ P0 (`docs/ARCHITECTURE_REPORT_MASTER_TZ_V3_P0.md`).  
That report was ownership/LOCK inventory. This report is **Meaning-First re-architecture**.

---

## 1. Target principle (TZ)

```
NOT:  Whisper Segment First
YES:  Meaning First → Sentence First → Paragraph Logic → Timing → Audio → Merge
```

After ASR, Whisper segments are **destroyed as identity**. Only `SemanticSentence` (and later paragraph blocks) own the pipeline unit.

---

## 2. As-is (honest)

```
Whisper.seg → [optional glue] → source_segments[i] + timing_map[i]
              → Translation / DSAL / TTS / Scheduler  (all keyed by i)
```

| Fact | Reality |
|------|---------|
| Whisper owns translation unit | **Yes** (post-merge STT chunks) |
| Word timestamps exist | **Partial** — `engines/word_timing_map/` when `word_timing` FF / `VM_WORD_TIMING_MAP` |
| Word = first-class UUID object (TZ P1) | **No** |
| Sentence Builder from words | **Missing** |
| SemanticSentence identity | **Missing** |
| Semantic graph / context engine | **Missing** (segment-indexed heuristics only) |
| Absolute rule: sentence never split | **Not enforced** (Whisper/merge can still cut mid-thought) |

### Primary ownership chain today

| Stage | Owner of unit | File evidence |
|-------|---------------|---------------|
| ASR | Whisper segment | `engines/stt_engine.py` |
| Glue | Still Whisper geometry | `engines/segment_merger.py` |
| Persist | `source_segments` + `timing_map` | `api/auto_dub_api.py` |
| Translate | Index `i` | translation pipeline / agents |
| Fit / TTS | Index `i` | DSAL, closed_loop, timing_fit |
| Identity | `segment_uuid` | `engines/pipeline_integrity/uuid_chain.py` |

Hard contract: `len(segments) != len(timing_map)` → `RuntimeError`.

---

## 3. What already helps P1+ (reuse, do not pretend done)

| Asset | Path | Use in V3 |
|-------|------|-----------|
| Word extract | `engines/word_timing_map/` | Seed for Word Timestamp Engine (P1) |
| WordToken model | `engines/word_timing_map/models.py` | Extend with UUID/phonemes/visemes |
| Sentence integrity validators | `engines/sentence_integrity.py` | Keep as gate; not a builder |
| Clause / block merge | `engines/dsal/` | Temporary duration tools; must move under Sentence Merge (P12) |
| Semantic Lock ancestor | Translation LOCK | Evolve to **Semantic Lock** (P7) — meaning/entities, not raw text freeze alone |
| Scheduler sole time owner | `engines/scheduler/` | Remains (P17) |
| Overflow/Underflow managers | `pipeline_integrity/*_manager.py` | Remap to sentence units (P18/P19) |

---

## 4. Conflicts with prior Freezes / ADR

| Document | Conflict |
|----------|----------|
| `docs/TRANSLATION_ENGINE_FREEZE_P1.md` | **Hard** — P0–P7 of this TZ rewrite Translation identity |
| `docs/TZ_WORD_TIMING_MAP.md` §5.3 | Keeps Whisper merge as timing core — **conflicts** with destroy-Whisper-segments |
| ADR-002 Audio-First | Text→LOCK→Audio; V3 is Meaning→…→Timing→Audio — compatible if “text” = SemanticSentence |
| ADR-005 FSM | Needs new states e.g. `WORDS_BUILT` → `SENTENCED` → `SEMANTIC_LOCKED` |
| Master TZ P12 deferred under Freeze | Superseded by this TZ’s Adaptation / Semantic Rewrite order |

**Required decision before P1:** issue `UNFREEZE-TE` for Semantic V3 spine, or run Semantic V3 as a parallel package (`engines/semantic_v3/`) until cutover.

---

## 5. P0 Definition (implementation scope when accepted)

P0 deliverables (next coding phase after acceptance):

1. After ASR completes, build word lattice (enable word timestamps by default for Semantic V3 path).
2. Mark Whisper segment list as **non-authoritative** (`asr_segments` archive only).
3. Introduce empty/skeleton types:
   - `SemanticWord`
   - `SemanticSentence`
4. Stop writing `source_segments` as the translation unit on the Semantic V3 feature flag path.
5. Architecture test: `assert translation_unit_type != "whisper_segment"` when flag on.
6. ADR: `ADR-010-meaning-first-sentences.md`.

**Out of scope for P0 coding (document only):** full Sentence Builder (P2), Translation rewrite (P5), Dub changes (P16).

---

## 6. Phase plan (strict gate)

| Phase | Title | Depends on |
|-------|-------|------------|
| **P0** | Whisper ≠ segment owner | **YOU ARE HERE** |
| P1 | Word Timestamp Engine | P0 |
| P2 | Sentence Builder | P1 |
| P3–P4 | Semantic Graph + Context | P2 |
| P5–P7 | Translation + Validation + Semantic Lock | P4 |
| P8–P9 | Timing Planner + Audio Predictor | P7 |
| P10–P13 | Adaptation ladder + Merge + Dynamic segments | P9 |
| P14–P16 | Word align + Lip + Dub | P13 |
| P17–P21 | Scheduler / Overflow / Underflow / Absolute rules | P16 |
| P22–P24 | Review / Quality / Learning | P21 |
| P25–P30 | Ownership / Determinism / Tests / Golden / Perf / DoD | continuous |

---

## 7. Risks if we “fix by if” inside AutoDub

- Index-parallel caches poison Meaning-First geometry  
- Studio/review UX breaks  
- Dual StreamDub stack drifts  
- Silent mid-sentence cuts return under old merger  

All such work must go through SemanticSentence contracts, not Whisper index patches.

---

## 8. Customer acceptance checklist

Please confirm:

1. [ ] This P0 Architecture Report accepted  
2. [ ] Proceed to **P1 Word Timestamp Engine** only (after P0 code skeleton) / or accept P0 code + P1 together  
3. [ ] Translation Freeze: **`UNFREEZE-TE` for Semantic V3** / **parallel package until cutover** (choose one)  
4. [ ] Word timestamps: **always on** for Semantic V3 path (recommended)  
5. [ ] StreamDub: **out of scope** / **must converge**  

---

## 9. References

- Spec: this chat — VideoMonster V3 Semantic TZ  
- Inventory agent findings (Whisper ownership, WTM gaps)  
- Related: `docs/TZ_WORD_TIMING_MAP.md`, `engines/word_timing_map/`, `engines/stt_engine.py`, `api/auto_dub_api.py`
