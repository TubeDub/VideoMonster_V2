# ETAP 1 — OSS Analysis: Live Translation Engine

**Date:** 2026-06-17  
**Decision status:** Adopted for TubeDub implementation

## Candidates

| Component | Option | License | Verdict |
|-----------|--------|---------|---------|
| URL ingest | **yt-dlp** | Unlicense | **Selected** — YouTube/Twitch/Vimeo |
| Demux / HLS / RTSP | **FFmpeg** | LGPL/GPL | **Selected** — already in project |
| Streaming STT | **faster-whisper** | MIT | **Selected** — same stack as batch STT |
| VAD | **Silero VAD** / energy gate | MIT | Phase 2 — MVP uses fixed chunks |
| MT | **Translation Manager** (in-repo) | — | **Selected** — reuse, no duplicate router |
| TTS | **edge-tts** (in-repo) | — | **Selected** — chunked synthesis |
| Player | **hls.js** / native video | BSD | UI Phase — Media Browser |

## Rejected / deferred

- Custom HLS parser — use FFmpeg
- Duplicate Marian router in `engines/live/` — call existing manager
- whisper-live server as separate process — integrate via chunk STT first

## TubeDub adapter layout

`engines/live/ingest.py` → `engines/live/audio.py` → `engines/stt_engine.transcribe` → `engines/live/translate.py` → `engines/tts.generate_audio`

## Risks

- yt-dlp not bundled — user install or future ModelManager component
- Live latency > 8s on CPU — use `VM_LIVE_STT_MODEL=tiny`, `VM_LIVE_SIMULATE_ONLY=1` for subs-only
