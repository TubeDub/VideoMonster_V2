"""Voice Clone — reference-sample binding for Cinema / StreamDub mode."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any

from engines.streamdub.base import ModuleCapabilities, StreamModule

logger = logging.getLogger("tubedub.voice_clone")


class VoiceCloneEngine(StreamModule):
    module_id = "voice_clone"

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        self._app_dir = Path(app_dir) if app_dir else Path.cwd()
        self._cfg = config or {}
        self._bank = self._app_dir / "output" / "voice_clones"
        self._bank.mkdir(parents=True, exist_ok=True)
        self._ready = True

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        return True, "ready", {"status": "ready", "bank": str(self._bank)}

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            features=["voice_cloning", "reference_bank"],
            meta={"status": "ready", "planned": False},
        )

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        ref = Path(str(payload.get("reference_audio") or payload.get("sample") or ""))
        voice_id = str(payload.get("voice_id") or payload.get("clone_id") or "").strip()
        text = str(payload.get("text") or "")

        clone_meta: dict[str, Any] = {"status": "passthrough"}
        ref_path: Path | None = None

        if ref.is_file():
            digest = hashlib.sha1(ref.read_bytes()[:65536]).hexdigest()[:12]
            voice_id = voice_id or f"clone_{digest}"
            dest = self._bank / f"{voice_id}{ref.suffix or '.wav'}"
            if not dest.exists():
                shutil.copy2(ref, dest)
            ref_path = dest
            clone_meta = {
                "status": "registered",
                "voice_id": voice_id,
                "reference_path": str(dest),
                "engine": "reference_bank_v1",
            }
            # Best-effort: if Edge-TTS style voice override exists, keep id for TTS stage.
            payload = {**payload, "tts_voice_hint": voice_id, "clone_reference": str(dest)}
        elif voice_id:
            matches = list(self._bank.glob(f"{voice_id}.*"))
            if matches:
                ref_path = matches[0]
                clone_meta = {
                    "status": "resolved",
                    "voice_id": voice_id,
                    "reference_path": str(matches[0]),
                    "engine": "reference_bank_v1",
                }
                payload = {**payload, "clone_reference": str(matches[0])}
            else:
                clone_meta = {"status": "missing_reference", "voice_id": voice_id}
        else:
            clone_meta = {
                "status": "passthrough",
                "message": "No reference_audio — using default TTS voice",
                "text_len": len(text),
            }

        # Minimal clone synth when adapter available + text + reference
        if text.strip() and ref_path and ref_path.is_file():
            try:
                from engines.voice_platform.cloning import clone_voice, get_clone_adapter

                adapter = get_clone_adapter()
                if adapter.is_available():
                    out = self._bank / f"{voice_id or 'clone'}_synth.wav"
                    result = clone_voice(text, str(ref_path), str(out))
                    if getattr(result, "ok", False) and out.is_file():
                        clone_meta = {
                            **clone_meta,
                            "status": "cloned",
                            "engine": getattr(adapter, "adapter_id", "clone"),
                            "output_path": str(out),
                        }
                        payload = {**payload, "cloned_audio": str(out)}
                    else:
                        clone_meta["clone_attempt"] = {
                            "ok": False,
                            "error": getattr(result, "error", "clone_failed"),
                            "adapter_id": getattr(adapter, "adapter_id", ""),
                        }
                else:
                    from engines.voice_platform.cloning import clone_readiness

                    ready = clone_readiness()
                    clone_meta["clone_attempt"] = {
                        "ok": False,
                        "error": ready.get("error_code") or "CLONE_ENGINE_MISSING",
                        "message": ready.get("message"),
                        "required_engines": ready.get("required_engines") or [],
                        "missing_engines": ready.get("missing_engines") or [],
                        "hint": ready.get("hint"),
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning("voice_clone synth skipped: %s", exc)
                clone_meta["clone_attempt"] = {"ok": False, "error": str(exc)[:200]}

        return {**payload, "voice_clone": clone_meta}
