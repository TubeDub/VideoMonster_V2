# Stage 20 — Ukrainian TTS Backends (tts_uk + Piper)

## Goal
Selectable high-quality Ukrainian TTS beside Edge:

1. **tts_uk** (RAD-TTS++ / Vocos) — recommended quality + duration control  
2. **piper** (`uk_UA-*-high`) — fast, CPU-friendly  
3. **edge** — default / fallback (`uk-UA-OstapNeural`)

## Implementation
| Piece | Location |
|-------|----------|
| Factory + voice maps + meta stamp | `engines/tts_backends.py` |
| tts_uk adapter | `engines/tts_engines/tts_uk_engine.py` |
| Piper length_scale + model resolve | `engines/tts_engines/providers.py` |
| Registry aliases / offline prefer | `engines/tts_engines/registry.py` |
| Pipeline + closed-loop regen engine_id | `api/auto_dub_api.py`, `engines/tts.py` |
| Duration estimate hook | `engines/text_slot_fit.estimate_tts_ms` + contextvar |
| UI backend + voice lists | `templates/dub.html`, `static/js/dub.js`, `templates/settings.html` |
| Catalog | `data/tts_engines.json`, `data/languages.py` |
| Tests | `tests/test_tts_backends.py` |

## Defaults
- Backend: **Edge** (`edge-offline`)
- Voice: **Ostap**
- UI hint: tts_uk / Piper recommended for natural Ukrainian

## Fallback
If `tts_uk` / `piper` missing or synth fails → Edge + warning log. Pipeline must not abort.

## Segment metadata
```json
{
  "tts_backend": "tts_uk",
  "tts_voice": "mykyta",
  "tts_sample_rate": 44100,
  "tts_engine": "tts_uk"
}
```

## Optional deps
```bash
pip install tts-uk
# Piper: piper CLI or pip install piper-tts
# Models: PIPER_MODELS_DIR with uk_UA-*-high.onnx
```

## Cold-run compare
Same George Jr. clip:
- `tts_engine=edge-offline` + `uk-UA-OstapNeural`
- `tts_engine=tts_uk` + `mykyta`
