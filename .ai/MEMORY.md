# Platform Memory

## Studio / Dub UI (2026-07-23)

- Whisper size lives in `#model-size` (hidden) + `#wizard-model-size` (start step); prepare uses `whisper_size`, start uses `model_size`.
- Settings `translateMode`: `/api/translate` online→Google, offline→Argos; `auto` keeps Universal pipeline.
- Post-dub editor is `/studio` (timeline mute→mix). Feature Dub Studio is `/dub-studio` (separate project mute/FX).

## Production hardening (2026-07-23)

- User media paths must resolve via `engines.path_safety.resolve_under_roots` under uploads/output.
- Project ZIP import uses `safe_extractall` (zip-slip blocked).
- Owner admin APIs require owner host + localhost; set `VM_OWNER_TOKEN` for license_server.
- License secret lives in `data/license_secret.txt` (auto-created); never ship a public HMAC constant.
- `VM_BIND_HOST=0.0.0.0` only when intentionally exposing the app on LAN.

## Module wiring pattern (MASTER TZ)

New module = engine service + `api/*_api.py` blueprint + Jinja/JS UI + `module_registry` route (no `/soon/`) + feature flag + optional TubeDub adapter + tests. Prefer wrapping existing engines over inventing parallel stacks.

## Translation Consistency

- AI Memory stores characters, glossary, style, voices per project
- Semantic Cache prevents redundant LLM calls
- Cross-episode memory via `global_memory.db` + `series_id`

## zh→uk / CJK collapse (`_tmp_3333`, 2026-07-23)

- Argos phrase loops → dirty + meaning_collapse; never TTS them
- Partial CJK tails need residual-script gate (dominance leak misses them)
- Integrity must scrub / not revert `foreign_script` to collapsed Raw MT
- Salvage scrub/LLM before hard TRANSLATION_TTS_BLOCKED
- Sparse single Whisper island on long CJK media → `split_overlong_cjk_segments`

## Phrase-loop heal (`555.zip`, 2026-07-24)

- Handoff UUID fix OK; STUDIO failed on segs 8/13 `meaning_collapse` = phrase_loop only
- `deflate_phrase_loop` → salvage + naturalizer + pre-STUDIO heal/re-TTS
- Valid UK with «у той момент»×N must continue, not brick the job

## TTS field desync / bleed (44.zip, 2026-07-24)

- Failure mode often NOT prefix-truncation: heal updates `text` but spoken `tts_text`/WAV stays corrupt
- `не міг не ` empty-strip left «молодший відчути» — fixed in `semantic_meaning.apply_compact_phrases`
- Pre-TTS: `heal_phrase_loops` + `repair_neighbor_bleed` before first synthesis; re-TTS after STUDIO heal
- Meaning Fit must not overwrite Raw MT `translated_text`; refuse destructive shorten
- Round-2: `soft_compress` must not `,`→`.` or chop clauses; raw debleed uses list index not `.get`; Review recomputes quality when score==0; TTS groups 1:1 under review-before-TTS
- Round-3: never stamp merged TTS group blob onto member audits/indices; SlotBudget text list must stay index-aligned; DSAL empty slots clear stale text; phrase-loop prefer clean approved/final; quality `"0"` triggers recompute
- Round-4 (GL Review): debleed must not map EN `and`→first «але»; complete EN_a keeps first UK sentence; «передсмертного» covers near-death; review-before-TTS freezes compress/MF; never strip насправді/дійсно/просто for TTS
- Round-5 (GL en→ru): DSAL clause phrases must be tgt_lang-aware — UK glue on RU TTS is a hard bug; final debleed before audits; RU `но`/`И вот`; Голлівуд→Голливуд
- Cross-lang isolation: any UK-only table (MF shorten, DSAL expand, pre_lock Насправді/молодший, compact phrases) must no-op unless `tgt_lang==uk`; never default unknown → uk for clause restore

## Language Validation P0 (2026-07-24)

- Gate is NOT langdetect-primary: script + confidence + meaning_collapse
- False «Language Mismatch» when expected=uk detected=uk came from collapsing semantic codes into LM
- Use `engines.language_validation.validate_language` — single interface
- Hard-stop only after recovery; soft semantic with target-lang OK → continue
- Diag files under `output/diagnostics/<task>/` packed into PassiveOpenDDF zip

## Performance Memory

- `performance.db` — hardware profile, benchmark, run history
- `analytics.db` — project run metrics
- `development_history.db` — architectural changes

## Knowledge Base

- `data/knowledge/knowledge_base.db` — best practices, lessons
- Populated by self-diagnostics and developer input
- All AI tools query via `get_knowledge_base()`

## Semantic Fingerprints

Each segment gets a hash — near-identical meaning skips LLM.

*Auto-updated by AI Memory `learn()` after each film.*
