# Translation Core — Part 3 Progress Report (P201–P220)

**Spec:** Master Technical Specification Part 3 — Translation Core v6.0  
**Date:** 2026-07-12  
**Status:** Implemented + Freeze (ADR-014)  

---

## Architecture

```
SemanticSentence
  → TranslationBackend (plugin)
  → Multi-Pass Variants
  → Semantic Evaluator
  → Completeness / Hallucination / Entity gates
  → Adaptation / Rewrite (pre-lock)
  → Semantic Lock
  → Translation Report
```

**Does not import:** Scheduler, Dub Engine, TTS, Merge, Studio.

---

## Checklist

| ID | Item | Status |
|----|------|--------|
| P201 | TranslationBackend interface | ✅ |
| P202 | Backend Registry | ✅ |
| P203 | Sentence-only input | ✅ |
| P204 | Multi-Pass variants | ✅ (`VM_TRANSLATION_VARIANTS`) |
| P205 | Semantic Evaluator | ✅ |
| P206 | Entity Preservation | ✅ |
| P207 | Terminology Manager | ✅ |
| P208 | Context Translation | ✅ |
| P209 | Style Preservation | ✅ (context/style scores) |
| P210 | Adaptive Translation | ✅ |
| P211 | Semantic Similarity | ✅ |
| P212 | Completeness Validator | ✅ |
| P213 | Hallucination Detector | ✅ |
| P214 | Rewrite Engine (pre-lock) | ✅ |
| P215 | Translation Confidence | ✅ |
| P216 | Semantic Lock | ✅ |
| P217 | Translation Report | ✅ |
| P218 | Golden Translation Tests | 🟡 unit suite; corpus expand |
| P219 | Regression Protection | ✅ architecture + unit tests |
| P220 | Translation Freeze | ✅ ADR-014 |

---

## Enable

```
set VM_SEMANTIC_V3=1
set VM_TRANSLATION_BACKEND=heuristic
```

Backends: `identity` | `heuristic` | `mt_bridge` | aliases (`nllb`,`marian`,`gpt`,…)

---

## Tests

```
pytest tests/test_translation_core_part3.py -q
```

---

## Next

Part 4 — Decision Policy Engine (adaptation strategy, Cost Model, Hard Constraints, Explainability, Policy Profiles).
