# VideoMonster V3 Semantic Engine — Progress Report

**Date:** 2026-07-12  
**Customer directive:** Semantic V3 Phase 2 (P31–P50)  

---

## Package

`engines/semantic_v3/`

Phase 1 spine + Phase 2 native Meaning Pipeline (`phase2.py`).

Wired in `api/auto_dub_api.py` after STT when `semantic_v3_enabled()`.

---

## Phase status

### Phase 1 (P0–P30)

| Phase | Status |
|-------|--------|
| P0 Whisper ≠ owner | ✅ archive |
| P1–P2 Words / Sentences | ✅ |
| P3–P7 Graph / Lock | ✅ |
| P8–P13 Predict / Adapt | ✅ (superseded by Phase 2 modules when flag on) |
| P18–P23 Absolute / QA | ✅ |
| Bridge `to_pipeline_arrays` | ⛔ **deprecated (P31)** |

### Phase 2 (P31–P50)

See **`docs/SEMANTIC_V3_PHASE2_REPORT.md`** and **`docs/adr/ADR-011-semantic-v3-phase2-freeze.md`**.

| Phase | Status |
|-------|--------|
| P31–P48 | ✅ under flag |
| P49 Golden | 🟡 unit suite; corpus expand |
| P50 Freeze | ✅ ADR-011 |

---

## How to enable

```
set VM_SEMANTIC_V3=1
set VM_SEMANTIC_V3_NATIVE_TE=1
```

---

## Tests

```
pytest tests/test_semantic_v3.py tests/test_semantic_v3_phase2.py -q
```
