"""P605 Voice Planner + P606 Multi-Speaker + P607 Identity + P608 Voice Memory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engines.voice_platform.types import SpeakerIdentity, VoicePlan
from engines.voice_platform.voice_registry import (
    get_style_profile,
    load_voice_registry,
    resolve_voice,
)
from engines.voice_platform.emotion import normalize_emotion
from engines.voice_platform.prosody import build_prosody_plan


class VoiceMemory:
    """P608 — remember assignments until project end; no silent voice swaps."""

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        self._speakers: dict[str, SpeakerIdentity] = {}
        self._locked = False

    def get(self, speaker_uuid: str) -> SpeakerIdentity | None:
        return self._speakers.get(speaker_uuid)

    def assign(
        self,
        speaker_uuid: str,
        voice_uuid: str,
        *,
        style_profile: str = "Documentary",
        emotion_profile: str = "calm",
        language: str = "",
        force: bool = False,
    ) -> SpeakerIdentity:
        existing = self._speakers.get(speaker_uuid)
        if existing and not force:
            if existing.voice_uuid != voice_uuid:
                raise ValueError(
                    f"VoiceMemory lock: speaker {speaker_uuid} already has "
                    f"voice {existing.voice_uuid}, cannot assign {voice_uuid}"
                )
            return existing
        ident = SpeakerIdentity(
            speaker_uuid=speaker_uuid,
            voice_uuid=voice_uuid,
            style_profile=style_profile,
            emotion_profile=normalize_emotion(emotion_profile),
            language=language,
            history=[{"event": "assign", "voice_uuid": voice_uuid}],
            consistency_score=100.0,
        )
        self._speakers[speaker_uuid] = ident
        return ident

    def record_use(self, speaker_uuid: str, *, emotion: str, tempo: float) -> None:
        ident = self._speakers.get(speaker_uuid)
        if not ident:
            return
        ident.history.append(
            {"event": "use", "emotion": emotion, "tempo": tempo}
        )
        # Soft consistency: emotion drift reduces score slightly
        if emotion and emotion != ident.emotion_profile and emotion != "calm":
            ident.consistency_score = max(0.0, ident.consistency_score - 0.5)

    def lock(self) -> None:
        self._locked = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "locked": self._locked,
            "speakers": {k: v.to_dict() for k, v in self._speakers.items()},
        }

    def save(self, path: Path | str) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: Path | str) -> "VoiceMemory":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        mem = cls(project_id=str(data.get("project_id") or ""))
        mem._locked = bool(data.get("locked"))
        for sid, row in (data.get("speakers") or {}).items():
            mem._speakers[sid] = SpeakerIdentity(
                speaker_uuid=str(row.get("speaker_uuid") or sid),
                voice_uuid=str(row.get("voice_uuid") or ""),
                style_profile=str(row.get("style_profile") or "Documentary"),
                emotion_profile=str(row.get("emotion_profile") or "calm"),
                history=list(row.get("history") or []),
                consistency_score=float(row.get("consistency_score") or 100),
                language=str(row.get("language") or ""),
            )
        return mem


def plan_voice_for_unit(
    *,
    speech_uuid: str,
    speaker_uuid: str,
    text: str,
    language: str = "ru",
    emotion: str = "calm",
    style: str = "Movie",
    preferred_voice: str | None = None,
    memory: VoiceMemory | None = None,
) -> VoicePlan:
    """P605 — decide voice/style/tempo/emotion/prosody before TTS."""
    mem = memory or VoiceMemory()
    style_prof = get_style_profile(style)
    emo = normalize_emotion(emotion or style_prof.emotion_default)

    existing = mem.get(speaker_uuid or speech_uuid)
    if existing:
        voice = resolve_voice(voice_uuid=existing.voice_uuid)
        style_name = existing.style_profile
        style_prof = get_style_profile(style_name)
    else:
        voice = resolve_voice(
            external_id=preferred_voice,
            language=language,
        )
        mem.assign(
            speaker_uuid or speech_uuid,
            voice.voice_uuid,
            style_profile=style,
            emotion_profile=emo,
            language=language or voice.language,
        )
        style_name = style

    prosody = build_prosody_plan(text, style=style_prof, emotion=emo)
    tempo = float(prosody.get("tempo") or style_prof.speech_rate)
    rate = prosody.get("rate_str")
    pitch = prosody.get("pitch_str")
    mem.record_use(speaker_uuid or speech_uuid, emotion=emo, tempo=tempo)

    return VoicePlan(
        speech_uuid=speech_uuid,
        speaker_uuid=speaker_uuid or speech_uuid,
        voice_uuid=voice.voice_uuid,
        provider=voice.provider,
        style=style_name if existing else style,
        tempo=tempo,
        emotion=emo,
        prosody=prosody,
        language=language or voice.language,
        external_voice_id=voice.external_id,
        rate=rate,
        pitch=pitch,
    )


def plan_multi_speaker(
    units: list[dict[str, Any]],
    *,
    project_id: str = "",
    default_style: str = "Movie",
    default_language: str = "ru",
    preferred_voices: dict[str, str] | None = None,
    memory: VoiceMemory | None = None,
) -> tuple[list[VoicePlan], VoiceMemory]:
    """
    P606 — assign each character a stable voice; never swap mid-project.
    ``units`` items: speech_uuid, speaker_uuid, text, emotion?, language?, style?
    """
    mem = memory or VoiceMemory(project_id=project_id)
    pref = preferred_voices or {}
    reg = load_voice_registry()
    pool = reg.list_voices(language=default_language) or reg.list_voices()
    # Prefer non-mock voices for real characters; keep mock as last resort
    pool = sorted(pool, key=lambda v: (v.provider == "mock", v.gender, v.external_id))
    speaker_order: list[str] = []
    for u in units:
        sp = str(u.get("speaker_uuid") or u.get("speaker") or u.get("speech_uuid") or "")
        if sp and sp not in speaker_order:
            speaker_order.append(sp)

    # Pre-assign preferred / round-robin distinct voices
    for i, speaker in enumerate(speaker_order):
        if mem.get(speaker):
            continue
        if speaker in pref:
            try:
                v = resolve_voice(external_id=pref[speaker])
                mem.assign(
                    speaker,
                    v.voice_uuid,
                    style_profile=default_style,
                    language=default_language,
                )
                continue
            except Exception:
                pass
        if pool:
            v = pool[i % len(pool)]
            # If pool collapses to one voice, still assign consistently
            mem.assign(
                speaker,
                v.voice_uuid,
                style_profile=default_style,
                language=default_language or v.language,
            )

    plans: list[VoicePlan] = []
    for u in units:
        speaker = str(u.get("speaker_uuid") or u.get("speaker") or u.get("speech_uuid") or "")
        plans.append(
            plan_voice_for_unit(
                speech_uuid=str(u.get("speech_uuid") or ""),
                speaker_uuid=speaker,
                text=str(u.get("text") or ""),
                language=str(u.get("language") or default_language),
                emotion=str(u.get("emotion") or "calm"),
                style=str(u.get("style") or default_style),
                preferred_voice=pref.get(speaker),
                memory=mem,
            )
        )
    return plans, mem


def assert_voice_consistency(memory: VoiceMemory) -> list[str]:
    """P622 — one speaker → one voice."""
    issues: list[str] = []
    seen_voice_for: dict[str, str] = {}
    for sid, ident in memory._speakers.items():
        if sid in seen_voice_for and seen_voice_for[sid] != ident.voice_uuid:
            issues.append(f"speaker {sid} voice drift")
        seen_voice_for[sid] = ident.voice_uuid
        # Same voice_uuid should not be forced-reassigned inconsistently in history
        assigns = [h for h in ident.history if h.get("event") == "assign"]
        voices = {h.get("voice_uuid") for h in assigns}
        if len(voices) > 1:
            issues.append(f"speaker {sid} multiple assign voices: {voices}")
    return issues
