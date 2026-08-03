# Stage 22 — Smart Expand (no garbage) + Dead-Air Killer + Mykyta Controls

## Goal
Close dead-air (underflow > 350 ms) with clean semantic expand only.  
Expose Mykyta voice controls (rate / pitch / volume / length_scale).  
Keep fill_ratio in 0.90–1.12 and reduce placement overlaps via ripple shift.

## Changes
- `engines/text_slot_fit.py` — `expand_to_fill` Stage 22: only `semantic_repeat_key` / `glossary_full_term` / `soft_pad_whitelist_once`; accept only fill ∈ [0.90, 1.12]; refuse garbage.
- `engines/closed_loop_timing.py` — expand when underflow > 350 or fill < 0.90; stamp `stage22` meta; OK band 0.90–1.12.
- `engines/tts_backends.py` + `engines/tts_engines/tts_uk_engine.py` — Mykyta controls wired into `synthesize`.
- `engines/conflict_resolver.py` — forced `ripple_shift` when placement overlap > 400 ms.
- UI: settings + dub wizard sliders for Mykyta; saved in `vm_settings` and sent on start.
- Tests: `tests/test_stage22_smart_expand.py`.

## Acceptance
- No «Саме про … тут ідеться» (or similar garbage expand).
- underflow > 350 → smart expand attempt.
- Mykyta sliders present and applied to `TtsUkEngine.synthesize`.
- Overlaps > 400 ms → ripple shift neighbors.
