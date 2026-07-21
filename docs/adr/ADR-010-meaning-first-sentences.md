# ADR-010 — Meaning First / Semantic Sentences

## Status
Accepted (VideoMonster V3 Semantic Engine P0)

## Context
Whisper segment boundaries were used as translation and dub units, causing
mid-sentence cuts, tail spill, and meaning loss.

## Decision
Whisper is ASR + word timestamp source only. After ASR, Whisper segments are
archived (`asr_archive`) and **SemanticSentence** becomes the sole pipeline unit.

Pipeline spine:
Meaning → Sentence → Paragraph logic → Timing → Audio → Merge.

Feature flag: `VM_SEMANTIC_V3=1` / `semantic_v3`.

## Consequences
- Translation Engine Freeze requires `UNFREEZE-TE` for deep TE changes; Semantic V3
  currently bridges via sentence-level `source_segments` arrays.
- FSM should later add `WORDS_BUILT` / `SENTENCED` / `SEMANTIC_LOCKED` (tracked).
- StreamDub remains out of this spine until a convergence ADR.
