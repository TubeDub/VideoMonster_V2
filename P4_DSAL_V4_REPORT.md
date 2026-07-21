# P4 DSAL v4.0 Report — Studio Editorial Pre-LOCK

**Date:** 2026-07-12  
**Scope:** TZ v4.0 P4 (band/delta visible, edit + re-DSAL + re-lock)

---

## Verdict

Translation Review is the pre-LOCK Studio editorial surface: users see **slot / Δ / band**, can edit text, DSAL refreshes on save, and `/relock` re-evaluates the LOCK gate for fixed segments.

---

## What shipped

### API / engine
| Piece | Detail |
|-------|--------|
| `engines/dsal/studio_editorial.py` | `refresh_dsal_on_segment`, `refresh_dsal_on_edits`, `relock_after_editorial` |
| `/apply` | Re-runs DSAL on edited segment |
| `POST .../translation_review/<id>/relock` | Re-evaluate LOCK gate |
| `build_translation_review` | Exposes `needs_studio`, `lock_gate_*`, project `translation_lock_deferred` |

### UI (`dub.js` + `dub.css`)
- DSAL badge always visible (not only dev): slot · Δ · band · match
- `needs_studio` tag + warn highlight
- Band colors: green / yellow / red

### Flow
```
Edit text → /apply → DSAL refresh → (optional) /relock → LOCK or still needs_studio
```

---

## Acceptance

| Criterion | Status |
|-----------|--------|
| band/delta visible in Review | ✅ |
| Edit before LOCK | ✅ (existing + DSAL refresh) |
| Re-lock after fix | ✅ `/relock` |
| Deferred segs marked needs_studio | ✅ |

---

## Tests

`tests/test_dsal_p3_p4.py` — editorial refresh + review fields. Full DSAL suite **25 passed**.
