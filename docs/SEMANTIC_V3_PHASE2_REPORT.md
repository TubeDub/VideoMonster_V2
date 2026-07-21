# Semantic V3 Phase 2 — Progress Report (P31–P50)

**Date:** 2026-07-12  
**TZ:** Semantic V3 — Phase 2 (Native Meaning Pipeline) v4.0  
**Status:** Implemented under feature flag  

---

## Architecture (after Phase 2)

```
ASR (Whisper archive only)
  → Words
  → Sentence Boundary Optimizer (P32)
  → Context Memory (P34)
  → Native Sentence Translation (P31/P33)
  → Semantic Lock
  → Word Alignment + Phoneme + Viseme (P35–P37)
  → Speech Duration Predictor (P38)
  → Adaptive Planning / Decision (P39–P40)
  → Dynamic Merge / Rewrite 2.0 (P41/P43)
  → Speech Units (P42)
  → Scheduler 2.0 → Audio Units / Timeline (P45–P47)
  → Quality Planner (P48)
```

Whisper is **not** segment owner. Bridge (`to_pipeline_arrays`) is **deprecated**.

---

## Module map

| Module | Phases |
|--------|--------|
| `boundary_optimizer.py` | P32 |
| `native_translate.py` | P31, P33 |
| `context_memory.py` | P34 |
| `word_alignment.py` | P35 |
| `phoneme_viseme.py` | P36–P37 |
| `duration_predictor.py` | P38 |
| `adaptive_planning.py` | P39–P41, P43 |
| `speech_units.py` | P42, P44 fields |
| `scheduler_v2.py` | P45–P47 |
| `quality_planner.py` | P48 |
| `phase2.py` | Orchestrator + export |
| ADR-011 | P49 Golden note + P50 Freeze |

---

## Phase checklist

| ID | Item | Status |
|----|------|--------|
| P31 | Remove bridge / native TE path | ✅ |
| P32 | Sentence Boundary Optimizer | ✅ |
| P33 | Translate SemanticSentence only | ✅ |
| P34 | Sentence Context Memory | ✅ |
| P35 | Word Alignment Engine | ✅ |
| P36 | Phoneme Engine | ✅ (deterministic G2P) |
| P37 | Viseme Engine | ✅ (coarse classes) |
| P38 | Speech Duration Predictor | ✅ phoneme/voice (not chars) |
| P39 | Adaptive Planning before TTS | ✅ |
| P40 | Decision order frozen | ✅ |
| P41 | Dynamic merge (config limit) | ✅ `VM_SEMANTIC_MAX_MERGE` |
| P42 | Speech Unit architecture | ✅ |
| P43 | Semantic Rewrite 2.0 | ✅ lock-gated |
| P44 | Audio Planning meta | ✅ via predictor + speech units |
| P45 | Scheduler 2.0 AudioUnits | ✅ |
| P46 | No Tail Spill | ✅ absolute_rules |
| P47 | No Double Voice | ✅ hard fail |
| P48 | Quality Planner | ✅ |
| P49 | Golden Pipeline | 🟡 harness + unit suite; full corpus expand later |
| P50 | Architecture Freeze | ✅ ADR-011 |

---

## Enable

```
set VM_SEMANTIC_V3=1
set VM_SEMANTIC_V3_NATIVE_TE=1
```

Optional: `VM_SEMANTIC_MAX_MERGE=5`

---

## Tests

```
pytest tests/test_semantic_v3.py tests/test_semantic_v3_phase2.py -q
```

---

## Explicit follow-ups (not silent)

1. Expand Golden Dataset across all P49 genres (interviews, film, series, docs, animation, YouTube).
2. Upgrade G2P to full IPA NLP where accuracy requires it.
3. Persist Learning Engine voice profiles beyond deterministic defaults.
4. Any post-freeze change to frozen modules → new ADR + PR + regression.
