# Changelog

## [2026-08-10] P0 — Soft-pad instead of audio_missing_fatal (TZ)

- Pre-mux Stage 23b: never `EXPORT_BLOCKED_MISSING_AUDIO` / never abort mux
- After 2× re-TTS (Mykyta→edge): silence pad `pad_silence_{segment_id}.wav` of slot length; mux always continues
- `final_status`: `ok` | `ok_with_pads` (not `audio_missing_fatal`); stamp `padded_count` / `padded_indices` from repair + soft-pad + segment flags
- Track = video duration; diagnostics include pad + duration fields
- Tests: `tests/test_soft_pad_mux_gate.py`

## [2026-08-10] P0 — Dub reaches video end (TZ)

- Pre-mux: re-TTS holes (no file / size<1000 / tts_ms==0 / needs_re_tts / split children); 2 attempts Mykyta→edge; silence pad fallback (no silent mux cut)
- Track master = ffprobe video_ms; pad silence to video_end; do not shrink to last segment
- Diagnostics: `video_duration_ms`, `track_duration_ms`, `tail_gap_ms`; warn `track_shorter_than_video` if gap>500
- Tests: `tests/test_dub_to_video_end_tz.py`

## [2026-08-09] P0 — Dub cut off before video end (timeline overshoot)

- Root cause: Stage19i `_allocate_times_speech_expanded` + neighbor shift pushed last segments past video; mux `-t` cut the ending (task 17a74f… seg23 start 182.8s > video 178.8s)
- `clamp_timeline_to_video_duration` + hard_end on speech-expanded splits; clamp before closed-loop exit / timed-track / studio mix
- Edge fallback: convert Mykyta float rate `0.97` → `-3%` (was `Invalid rate '0.97'`)
- Tests: `test_clamp_timeline_pulls_segments_past_video_end`, `test_allocate_times_video_hard_end`

## [2026-08-08] P0 — Audio never deleted + Stage23 duration control

- `cleanup_after_dub_complete`: never wipe session_dir; `keep_segment_audio=True`; salvage `slot_fit_`/`pause_run_`/`tts_` + wav/mp3 from work dirs
- Pre-mux `_assert_audio_file` + re-TTS; residual holes → silence pad + mux continues (`ok_with_pads`; was fatal — superseded 2026-08-10)
- `tts_pipeline` census uses disk size≥1000, stamped before cleanup
- Underflow >250 / fill <0.92 ⇒ `duration_control_used` ≠ `none` (length_scale/rate/expand/atempo)
- Tests: `tests/test_pipeline_cleanup_keep_audio.py`, stage23b / duration_control

## [2026-07-25] P0 — TTS↔Review align (15×): oзвучка = Final, debleed at populate

- Root cause: Review Final shown at pause; after resume DubbingEngine/SlotBudget rewrote spoken text
- `engines/tts_review_align.py`: debleed Raw/Final audits + phrase-loop deflate at Review populate
- review-before-TTS: freeze DubbingEngine text mutations; re-freeze after SlotBudget; TTS = Final (+ terminal punct)
- Pipeline: debleed `post_naturalizer` column (was only naturalized/raw)
- Tests: `scripts/test_tts_review_align.py` — battery 15× green (with r4/r5/xlang)

## [2026-07-25] P0 — Cross-lang isolation (15×): no UK glue on other targets

- Pre-LOCK polish / DSAL expand-compress / MF shorten-expand gated by `tgt_lang`
- Compact phrases split UK/RU/EN tables; TTS guard uses segment language
- Clause restore refuses unknown lang (no default-to-uk inject)
- Removed harmful `молодший. Сьогодні` insert in `uk_name_forms`
- Tests: `scripts/test_cross_lang_isolation.py` — battery 15× green

## [2026-07-25] P0 — TTS Round-5 (15×): en→ru Review bleed / UK orphan on RU

- Clause restore is language-aware (UK/RU maps); never append «досвід на межі смерті» into Russian
- «околосмертного» covers near-death for RU; `strip_cross_lang_clause_orphans` on DSAL + naturalize_ru
- Debleed: RU `но/однако` + discourse `И вот`/`Две недели`/`Итак`; final debleed gate before audits
- Fix `Голлівуд` → `Голливуд` in RU naturalizer
- Tests: `scripts/test_tts_round5_ru.py` — battery 15× green

## [2026-07-24] P0 — TTS Round-4 (15×): Review bleed / near-death / Jr / freeze

- Debleed: EN `and` no longer splits on first UK «але»; sentence-first for complete EN_a (dinner/crash)
- Jr false stop: «Джордж-молодший. Сьогодні» → continuous; near-death orphan strip always
- Clause coverage: «передсмертного» covers near-death (no TTS junk append)
- review-before-TTS freezes soft_compress + MF safety-net; MF/soft no longer strip насправді/дійсно/просто
- Review UI: prefer Final when TTS looks truncated; source-aware shared-blob split
- Tests: `scripts/test_tts_round4_review.py` — battery 15× green

## [2026-07-24] P0 — TTS Round-3 (15×): group-blob / slot index / DSAL / quality

- `_sync_tts_audits_from_groups` + `tts_inputs_by_seg`: never stamp merged group blob onto member indices
- SlotBudget rebuild keeps index alignment with `segments_data` / timing (empty for merged/archived)
- DSAL redistribute: clear empty slots + mark `merged_into` (no stale neighbor leftovers)
- soft_compress + semantic TTS adapt stamp via `stamp_authoritative_final_text` / audits
- Review quality: recompute on `"0"`; computed qd wins over stale zeros
- Mid-clause period repair (`їхав. Джордж`); phrase-loop heal prefers clean approved/final
- Tests: `scripts/test_tts_round3_guards.py` — battery 15× green

## [2026-07-24] P0 — TTS Round-2 (15×): soft_compress / debleed / quality

- `soft_compress_for_slot`: no `, `→`. `; no clause chop; skip if MF refused
- Fix raw debleed: `raw_by_index` is a list (`.get` was silently skipping)
- Shared-blob split in `tts_text_guard`; MF syncs `tts_text`; integrity stamps `segments_data`
- Review quality recompute when score is 0; TTS groups 1:1 when review-before-TTS
- Mid-clause punctuation: do not force `.` after «їхав/коли/що…»
- Tests: `scripts/test_tts_round2_guards.py`

## [2026-07-24] P0 — TTS truncation / bleed / bare infinitive (44.zip)

- Fix `apply_compact_phrases`: never strip «не міг не » → bare «відчути»
- Pre-TTS guard: phrase-loop deflate + neighbor prefix-bleed restore (`tts_text_guard`)
- Meaning Fit refuses destructive shorten (>35% / truncated_tail)
- `stamp_authoritative_final_text` syncs `tts_text` + deflates loops
- RCA: `output/dev/rca_44_tts_shift.md`; tests: `scripts/test_tts_truncation_bleed_p0.py`

## [2026-07-24] P0 — Unified Language Validation + Recovery

- Single service `engines/language_validation/` (confidence, entity mask, neighbor vote)
- `expected==detected` never reported as Language Mismatch (semantic → phrase_loop/meaning_collapse)
- Full-text scoring (not head-only); brands/names/abbr masked (Lucas, USC, Fiat, OpenAI…)
- Recovery before hard-stop: deflate → naturalizer → salvage → revalidate (PRE_TTS + STUDIO)
- Diagnostic ZIP extras: `language_validator.log`, `confidence_scores.json`, `recovery_trace.json`, `decision_trace.json`
- Tests: `scripts/test_language_validation_p0.py` + 555.zip studio simulation

## [2026-07-24] Fix — STUDIO phrase-loop meaning_collapse (555.zip)

- `deflate_phrase_loop` collapses Argos/closed-loop repeats («у той момент»×N)
- Salvage / naturalizer / TTS integrity heal loops before hard-fail
- STUDIO gate: heal + re-TTS affected segs instead of `LANGUAGE_MISMATCH` brick
- Regression: `scripts/test_phrase_loop_deflate.py` (555.zip segs 8/13)

## [2026-07-23] Cloud/offline hard-gates + clone MVP

- `target=cloud` → honest 501 with RU/EN (`CloudTargetUnavailableError`); TubeDub Cloud without URL reports local-mirror-only
- OAuth authorize / provider meta: `message_ru` + never `oauth_connected` without token
- Offline dub: `VM_TTS_MODE`/`VM_DUB_MODE=offline` prefers Piper, blocks online TTS; MT skips deep fallback under offline lock
- Catalog: Piper + Coqui in `data/tts_engines.json`; system health Piper probe
- Voice clone: Coqui/XTTS bridge + real capability probe; StreamDub bank tries clone synth when adapter present
- Smoke: `tests/test_remote_jobs_offline_smoke.py` (no API keys)

## [2026-07-23] UI visual absolute — 15-round sky/Jakarta polish

- Shared SVG kit `static/js/ui_icons.js` + `.ui-ico` / `.status-dot` tokens in `style.css`
- Kill emoji-chrome across base/nav, index, projects, dub wizard, studio, voice, translate, settings, director, plugins, cloud, platform, monitoring/dev, error/mini/reader accents
- Plus Jakarta Sans + sky `#5b9cf5` consistency; remove indigo/purple leftovers; production density
- Projects API icons → semantic keys (`film`/`speaker`/`book`); no auto_dub engine core changes

## [2026-07-23] TTS / voice / dub mux production harden

- Fail empty/low-coverage TTS handoff (`TTS_HANDOFF_EMPTY`) instead of shipping 1s silent timed.mp3
- Align parallel TTS group timeout with segment timeout (default 120s via `VM_PIPELINE_SEGMENT_TIMEOUT_SEC`)
- Wire Voice Platform assign/plan into auto-dub pre-TTS; honor per-segment `assigned_voice` in parallel/sequential TTS
- Voice API: `/api/voice/clone|assign|plan|memory` + clone status; upload 50MB cap
- TTS API: rate/pitch/engine_id/emotion + timing_mode / total_duration
- Remux default mix_mode `full_dub`; dub `/api/dub/check` uses bundled `find_ffmpeg`
- Online cloud TTS catalog opt-in: `VM_ENABLE_ONLINE_TTS=1` (keys alone no longer mark available)
- Gate debug NDJSON behind `VM_DEBUG_NDJSON`; remove hardcoded `debug-ee98a6` hot-path writes
- Legacy dub TASKS best-effort persist to `output/dub_legacy_tasks.json`
- Tests: `tests/test_tts_voice_dub_harden.py`

## [2026-07-23] Studio cycle — Whisper UI / dub-studio mute-FX / translateMode

- Dub wizard: visible Whisper model selector (tiny→large), syncs to prepare `whisper_size` + start `model_size`
- `/dub-studio`: track mute / solo / volume + click-to-add FX on selected track (does not touch `/studio`)
- `/api/translate`: honors Settings `mode` — online=Google, offline=Argos; auto keeps Universal pipeline
- Studio UI uses `getTranslateMode()` for translate requests

## [2026-07-23] Fix — zh→uk LANGUAGE_MISMATCH / meaning_collapse production brick

- Residual CJK in mostly-UK lines fails script guards (`residual_source_script`)
- Argos phrase loops flagged via `has_phrase_loop` / dirty MT; LLM strip + rescue
- `sentence_integrity`: scrub foreign script; never fall back to collapsed Raw MT
- Language gate: salvage (scrub / LLM) before blank-all / TRANSLATION_TTS_BLOCKED
- `split_overlong_cjk_segments`: rescue single Whisper drama blob into multi-turn STT
- Tests: `tests/test_zh_uk_meaning_collapse_recovery.py` (`_tmp_3333` shapes)

## [2026-07-23] Remote marketplace + browser mic capture

- Remote plugin storefront: `VM_PLUGIN_MARKETPLACE_URL` catalog fetch + zip install with path/zip-slip safety; hard-gate when unset (local remains default)
- Platform Recording Studio: getUserMedia + MediaRecorder punch-in/out upload to `/api/recording`

## [2026-07-23] Second-pass unfinished modules

- Online TTS: OpenAI / ElevenLabs / Azure / Google / Studio HTTP (`engines/tts_engines/online_engines.py`); Piper/Coqui synthesize when backends present
- Local remote jobs execute translate / whisper / tts / audio / render-mux (`engines/cloud/remote_jobs.py`)
- CloudTranslator Anthropic + Gemini HTTP; mirror providers rename/move/archive
- Live interface readiness YELLOW; Realtime Interpreter + Screen Dub MVP (`engines/interpreter` + platform API/UI)
- Plugin Marketplace UI: catalog + path/zip install on `/plugins`

## [2026-07-23] Absolute loop — OAuth / live stream / edge cases

- Cloud OAuth scaffolding: Google/OneDrive/Dropbox env credentials, authorize/callback/disconnect, hard-gate when secrets missing (no fake remote “connected”); local mirror remains offline fallback
- Live/streaming: preflight (FFmpeg/STT), capabilities probe, file→RTMP + gdigrab screen path, RTSP/RTMP ingest passthrough, honest engine errors
- Director UI: recent reports list + safe 404; Marketplace merges curated catalog; Assistant trace missing → clear error
- Native VST2/VST3 still deferred (FFmpeg FX presets remain production path)

## [2026-07-23] Stub / Coming Soon modules → production wiring

- `/soon/*` redirects to real modules; `module_registry` routes: studio/voice/director/live/cloud/plugins
- TubeDub adapters filled: enterprise_translation, word_timing, professional_dubbing, developer_tools, cloud_platform, live_translation
- Cloud Drive/OneDrive/Dropbox/TubeDubCloud: filesystem mirror providers (full CRUD)
- StreamDub lip_sync (duration/offset) + voice_clone (reference bank)
- VST host: FFmpeg FX presets as production path
- AI Director UI `/director` + `POST /api/director/validate`
- Marketplace curated catalog `data/plugin_marketplace_catalog.json` + plugins UI install
- Feature flags BETA/enabled: cloud, dub_studio, live, ai_director, user_recording, assistant
- Platform UI panels: recording / streaming / broadcast
- Tests: `tests/test_stub_modules_completion.py`

## [2026-07-23] Infra APIs — import/storage/system/license/owner/translate

- Import API: meta.json no longer preferred over media; list/delete; import_id validation; atomic meta
- Storage export: Windows-safe temp name (`*.zip.writing`) + `os.replace`; save returns 423 when locked
- System check: ffprobe, `find_ffmpeg`, robust disk probe, version payload
- License API: structured `ok` responses; atomic license JSON persistence
- Translation reports: `/reports`, `/project/<uuid>`, path allowlist, restart-safe fallback
- Owner open-folder: allowlist under app/storage roots; sanitize build_id download
- Translate: sanitized uploads, atomic VMR, STT temp audio cleanup
- `path_safety.clamp_write_path` preserves `.vmproj.zip`; voice resolve uses roots list

## [2026-07-23] Plugins / Director / Assistant / Recording lane

- Local marketplace: zip+dir install/update with manifest validation and backup restore
- Plugin invoke / registrations / catalog HTTP endpoints
- Builtin plugins wired to real engines (whisper, edge_tts, MT, ollama, LLM providers, subtitle, voice_clone, lip_sync, elevenlabs)
- Director / Semantic / Grammar APIs: list, summary, per-segment, disk fallback
- AI Assistant: text-review, trace analyze, fix_calque
- Recording: upload, FX via RecordingStudioSession, session persistence
- DevAssistant API: test / post-change / self-diagnose

## [2026-07-23] Production hardening — security / crash paths

- Default Flask bind `127.0.0.1` (`VM_BIND_HOST` to open LAN)
- License/owner admin: `is_owner_host` + localhost-only; license server rejects default token
- Path allowlists + zip-slip-safe project import (`engines/path_safety.py`)
- Plugin enable/disable/permissions require developer mode
- License HMAC: auto-create per-install secret (no public fallback)
- Heavy blueprint failures surfaced in `/api/system/check`
- Removed agent NDJSON debug writers from desktop/dub/streamdub hot paths

## [2026-07-23] UI — production visual polish

- Unified design tokens in `static/css/style.css` (`--card`, `--hover`, `--warning`, spacing scale)
- Replaced Inter with Plus Jakarta Sans + IBM Plex Mono; toned down purple glow / gradient chrome
- Polished app shell (`templates/base.html`): SVG logo mark, quieter sidebar, aligned prepare overlay
- Studio editor chrome (`studio.css` / `studio.html`): denser toolbar, custom scrubber, timeline hierarchy
- Home + Projects surfaces: less emoji chrome, tighter cards/filters, clearer hierarchy
- Dub wizard: token-aligned tiles/drop zone, pill step dots, no glow on selection

## [2026-07-08] Stage 10 — Autonomous Development Platform

- Added Project Brain (`.ai/`)
- Added `DevAssistant` unified API
- Added Architecture Engine, Change Impact Analyzer
- Added Technical Debt Monitor, Code Reviewer, Task Planner
- Added Recommendation Engine, Documentation Sync
- Added Knowledge Base + Development History DB

## [2026-07-08] Stage 9 — Plugin System + SDK

- Plugin Manager, capability system, marketplace API stub
- SDK with `register_*` hooks, template, documentation

## [2026-07-08] Stage 8 — Monitoring Center

- Live dashboard, bottleneck analyzer, diagnostics
- Analytics DB, report export

## [2026-07-08] Stage 7 — Performance Optimizer

- Hardware Profiler, Benchmark Engine, dynamic resource plan

## [2026-07-08] Stage 6 — AI Memory

- Semantic Cache, character/glossary dictionaries

## Earlier Stages

See `docs/` for Stages 1–5 documentation.

*New entries appended by `assistant.document()` or manually.*

## [2026-07-08] Documentation sync
- Documentation auto-sync

## [2026-07-08] Documentation sync
- Documentation auto-sync

## [2026-07-08] Documentation sync
- Documentation auto-sync

## [2026-07-08] Documentation sync
- Documentation auto-sync

## [2026-07-08] Documentation sync
- Documentation auto-sync

## [2026-07-08] Documentation sync
- Documentation auto-sync

## [2026-07-14] Documentation sync
- Documentation auto-sync
