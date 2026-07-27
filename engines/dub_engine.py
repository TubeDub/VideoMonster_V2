"""
TubeDub / VideoMonster Engine — Dub Engine
Заменяет или микширует аудиодорожку видео через FFmpeg.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from engines.audio_mix_config import AudioMixConfig


# Пресеты микширования (POST-REAL-TEST)
MIX_PRESETS: dict[str, dict[str, float]] = {
    "full_dub": {"original": 0.0, "dub": 1.0, "background": 0.0},
    "atmosphere": {"original": 0.08, "dub": 1.0, "background": 0.08},
    "language_learning": {"original": 0.38, "dub": 1.0, "background": 0.38},
}

# EBU R128 loudness normalization for the final dub audio track.
# -16 LUFS is comfortable for streaming/YouTube.  TP=-1.5 dBTP prevents clipping.
_LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"

# Audio ducking: background lowered by this many dB during speech, then restored.
# Fade-in (before speech): 150 ms.  Fade-out (after speech): 300 ms.
_DUCKING_DB: float = -8.0          # how much to duck background during speech
_DUCKING_FADE_IN_MS: int = 150
_DUCKING_FADE_OUT_MS: int = 300

# Без stem separation «фон» = та же дорожка, что и оригинал (см. UI-подсказку).
ATMOSPHERE_LIMITATION = (
    "Режим «Атмосфера»: без разделения вокала и музыки оригинальная дорожка "
    "приглушается целиком (~8%), дубляж накладывается поверх."
)


def resolve_mix_volumes(
    mix_mode: str = "full_dub",
    *,
    original_volume: float | None = None,
    dub_volume: float | None = None,
    background_volume: float | None = None,
    legacy_mode: str | None = None,
    mix_volume: float | None = None,
) -> tuple[str, float, float, float, list[str]]:
    """
    Возвращает (effective_mode, original, dub, background, warnings).
    Поддерживает legacy mode='replace'|'mix' + mix_volume.
    """
    warnings: list[str] = []
    mode = (mix_mode or "full_dub").strip().lower()

    if mode in ("replace", "mix"):
        legacy_mode = mode
        mode = "full_dub" if mode == "replace" else "language_learning"

    if legacy_mode == "replace" and mix_mode in ("", "full_dub", None):
        mode = "full_dub"
    elif legacy_mode == "mix" and mix_mode in ("", "full_dub"):
        vol = 0.3 if mix_volume is None else float(mix_volume)
        mode = "custom"
        original_volume = vol
        dub_volume = 1.0

    if mode == "custom":
        orig = 0.0 if original_volume is None else float(original_volume)
        dub = 1.0 if dub_volume is None else float(dub_volume)
        bg = orig if background_volume is None else float(background_volume)
        if bg != orig:
            warnings.append(
                "Отдельная дорожка музыки недоступна без stem separation; "
                "ползунок «фон» управляет общей громкостью оригинала."
            )
            orig = max(orig, bg)
        # Under full-level dub, linear 0.20 is nearly inaudible — lift mid underlay
        # so UI "20%" is actually hearable without drowning the dub.
        if 0.05 < orig <= 0.35 and dub >= 0.85:
            orig = min(0.55, orig * 1.6)
    elif mode in MIX_PRESETS:
        preset = MIX_PRESETS[mode]
        orig = preset["original"]
        dub = preset["dub"]
        bg = preset["background"]
        # Explicit UI/API override must win over full_dub mute.
        if original_volume is not None and float(original_volume) > 0.001:
            mode = "custom"
            orig = float(original_volume)
            if dub_volume is not None:
                dub = float(dub_volume)
            bg = orig if background_volume is None else float(background_volume)
            if 0.05 < orig <= 0.35 and dub >= 0.85:
                orig = min(0.55, orig * 1.6)
        elif mode == "atmosphere":
            warnings.append(ATMOSPHERE_LIMITATION)
    else:
        warnings.append(f"Неизвестный mix_mode={mode!r}, используется full_dub.")
        mode = "full_dub"
        orig, dub, bg = 0.0, 1.0, 0.0

    orig = max(0.0, min(1.0, orig))
    dub = max(0.0, min(2.0, dub))
    bg = max(0.0, min(1.0, bg))
    return mode, orig, dub, bg, warnings


class DubEngine:
    """
    Движок дубляжа видео.
    Режимы mix_mode:
      full_dub           — оригинал 0%, дуб 100% (по умолчанию)
      atmosphere         — оригинал ~8%, дуб 100%
      language_learning  — оригинал ~38%, дуб 100%
      custom             — пользовательские ползунки
    Legacy: mode='replace'|'mix' + mix_volume.
    """

    def __init__(
        self,
        video_path: str = "",
        audio_path: str = "",
        timed_audio: str = "",
        timing_map: list[str] | None = None,
        subtitles: str = "",
        video_stretch_segments: list[dict] | None = None,
        # Audio ducking: lower background during speech, restore after
        ducking_enabled: bool = False,
        ducking_db: float = _DUCKING_DB,
        ducking_fade_in_ms: int = _DUCKING_FADE_IN_MS,
        ducking_fade_out_ms: int = _DUCKING_FADE_OUT_MS,
        # Speech intervals for ducking: list of {start_ms, end_ms}
        speech_intervals: list[dict] | None = None,
        # Optional music+SFX stem from source separation (no original speech)
        background_audio_path: str = "",
        background_attenuation_db: float = 4.5,
        # Optional isolated ORIGINAL VOICE stem (vocals) from source separation.
        # When present + original_volume>0 the original voice can be underlaid and
        # ducked independently of music/SFX (professional cinema mix).
        dialogue_audio_path: str = "",
        # Resolved professional-mix policy (levels + intelligent ducking).
        mix_config: "AudioMixConfig | None" = None,
    ):
        self.video_path = video_path
        self.audio_path = audio_path
        self.timed_audio = timed_audio
        self.timing_map: list[str] = timing_map or []
        self.subtitles = subtitles
        self.video_stretch_segments: list[dict] = video_stretch_segments or []
        self.ducking_enabled = ducking_enabled
        self.ducking_db = ducking_db
        self.ducking_fade_in_ms = ducking_fade_in_ms / 1000.0
        self.ducking_fade_out_ms = ducking_fade_out_ms / 1000.0
        self.speech_intervals: list[dict] = speech_intervals or []
        self.background_audio_path = str(background_audio_path or "").strip()
        self.background_attenuation_db = float(background_attenuation_db)
        self.dialogue_audio_path = str(dialogue_audio_path or "").strip()
        self.mix_config = mix_config
        from engines.ffmpeg_paths import find_ffmpeg

        self._ffmpeg = find_ffmpeg()
        self._ffprobe = shutil.which("ffprobe")

    def validate(self) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if not self._ffmpeg:
            errors.append("FFmpeg не найден. Установите FFmpeg и добавьте в PATH.")
        if not self.video_path or not Path(self.video_path).exists():
            errors.append("Видеофайл не найден: " + str(self.video_path))
        audio = self._audio_path()
        if not audio or not Path(audio).exists():
            errors.append("Аудиофайл не найден: " + str(audio))
        return len(errors) == 0, errors

    def run(
        self,
        output_path: str,
        mode: str = "replace",
        mix_volume: float = 0.3,
        mix_mode: str = "full_dub",
        original_volume: float | None = None,
        dub_volume: float | None = None,
        background_volume: float | None = None,
        progress_callback: Optional[Callable[[float], None]] = None,
        timeout_sec: int = 600,
    ) -> tuple[bool, str, list[str]]:
        """
        Запускает дубляж.

        :param mix_mode: full_dub | atmosphere | language_learning | custom
        :param mode: legacy 'replace' | 'mix' (если mix_mode не задан явно)
        :return: (успех, путь_к_файлу, [ошибки/предупреждения])
        """
        ok, errors = self.validate()
        if not ok:
            return False, "", errors

        effective_mode, orig_vol, dub_vol, _bg, preset_warnings = resolve_mix_volumes(
            mix_mode,
            original_volume=original_volume,
            dub_volume=dub_volume,
            background_volume=background_volume,
            legacy_mode=mode,
            mix_volume=mix_volume,
        )

        audio = self._audio_path()
        duration = self._get_duration()
        warnings: list[str] = list(preset_warnings)

        has_video_stretch = bool(
            self.video_stretch_segments and
            any(float(s.get("stretch_ratio", 1.0)) > 1.01 for s in self.video_stretch_segments)
        )
        cmd = self._select_mix_command(
            audio, output_path, effective_mode, orig_vol, dub_vol,
            has_video_stretch, warnings,
        )

        try:
            proc = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            stderr_lines: list[str] = []
            deadline = time.time() + max(60, int(timeout_sec))
            for line in proc.stderr:
                stderr_lines.append(line)
                if progress_callback and duration and "time=" in line:
                    m = re.search(r"time=(\d+):(\d+):(\d+\.?\d*)", line)
                    if m:
                        t = (
                            int(m.group(1)) * 3600
                            + int(m.group(2)) * 60
                            + float(m.group(3))
                        )
                        pct = min(99.0, t / duration * 100)
                        progress_callback(pct)

                if time.time() > deadline:
                    proc.kill()
                    proc.wait(timeout=5)
                    return (
                        False,
                        "",
                        [
                            f"FFmpeg превысил лимит {timeout_sec} с.",
                            "".join(stderr_lines[-10:]).strip(),
                        ],
                    )

            proc.wait(timeout=30)

            if proc.returncode != 0:
                last = "".join(stderr_lines[-10:])
                return (
                    False,
                    "",
                    [
                        f"FFmpeg завершился с кодом {proc.returncode}.",
                        last.strip(),
                    ],
                )

            if progress_callback:
                progress_callback(100.0)

            return True, output_path, warnings

        except FileNotFoundError:
            return False, "", ["FFmpeg не найден в системе."]
        except Exception as e:
            return False, "", [f"Ошибка FFmpeg: {e}"]

    # ─── Video time-stretch helpers ───────────────────────────────────────────

    def _build_video_setpts_filter(
        self,
        segments: list[dict],
        total_duration_sec: float,
    ) -> str | None:
        """
        Build an ffmpeg filter_complex string that slows specific video segments
        (setpts = PTS * ratio) to accommodate longer TTS audio.

        Only segments with stretch_ratio > 1.01 are actually stretched.
        The result is a concat of: [pre_segment][slowed][post_segment]...

        Returns None if there are no segments to stretch or only one stretch point,
        as the simpler -c:v copy path should be used instead.

        Example for one segment [5s–8s] with stretch_ratio=1.1:
          [0:v]trim=0:5,setpts=PTS-STARTPTS[v0];
          [0:v]trim=5:8,setpts=(PTS-STARTPTS)*1.1[v1];
          [0:v]trim=8,setpts=PTS-STARTPTS[v2];
          [v0][v1][v2]concat=n=3:v=1:a=0[vout]
        """
        segs = sorted(
            [s for s in segments if float(s.get("stretch_ratio", 1.0)) > 1.01],
            key=lambda s: s["start_ms"],
        )
        if not segs:
            return None

        parts: list[str] = []
        labels: list[str] = []
        cursor_sec = 0.0
        part_idx = 0

        for seg in segs:
            s_sec = seg["start_ms"] / 1000.0
            e_sec = seg["end_ms"] / 1000.0
            ratio = float(seg["stretch_ratio"])

            # Clip to valid range
            s_sec = max(cursor_sec, s_sec)
            e_sec = min(max(s_sec + 0.1, e_sec), total_duration_sec)
            if e_sec <= s_sec:
                continue

            # Normal-speed part before this segment
            if s_sec > cursor_sec + 0.01:
                lbl = f"v{part_idx}"
                parts.append(
                    f"[0:v]trim={cursor_sec:.3f}:{s_sec:.3f},setpts=PTS-STARTPTS[{lbl}]"
                )
                labels.append(f"[{lbl}]")
                part_idx += 1

            # Slowed part (video plays at 1/ratio normal speed → PTS grows faster)
            lbl = f"v{part_idx}"
            parts.append(
                f"[0:v]trim={s_sec:.3f}:{e_sec:.3f},"
                f"setpts=(PTS-STARTPTS)*{ratio:.4f}[{lbl}]"
            )
            labels.append(f"[{lbl}]")
            part_idx += 1
            cursor_sec = e_sec

        # Tail after last stretched segment
        if cursor_sec < total_duration_sec - 0.01:
            lbl = f"v{part_idx}"
            parts.append(
                f"[0:v]trim={cursor_sec:.3f},setpts=PTS-STARTPTS[{lbl}]"
            )
            labels.append(f"[{lbl}]")
            part_idx += 1

        if not labels:
            return None

        n = len(labels)
        concat = f"{''.join(labels)}concat=n={n}:v=1:a=0[vout]"
        return ";".join(parts) + ";" + concat

    def _select_mix_command(
        self,
        audio: str,
        output_path: str,
        effective_mode: str,
        orig_vol: float,
        dub_vol: float,
        has_video_stretch: bool,
        warnings: list[str],
    ) -> list[str]:
        """
        Choose the FFmpeg command for the requested mix (single decision point —
        keeps all routing in one place so auto-dub / studio / preview never diverge).

        Routing (TZ Tasks 1–4, 8):
          * stem + underlay + isolated voice → stem voice-duck mix
              (music/SFX alive, original voice ducked only while dub speaks)
          * stem + muted original            → stem mix (music/SFX + dub, no voice)
          * muted original, no stem          → replace (dub only)
          * underlay, no stem                → sidechain-ducked full-original mix
        """
        use_stem_background = self._has_background_stem()
        use_dialogue_stem = self._has_dialogue_stem()
        mute_original = effective_mode == "full_dub" or orig_vol <= 0.001

        if use_stem_background and not mute_original and use_dialogue_stem:
            if has_video_stretch:
                warnings.append(
                    "Видео-растяжение недоступно в режиме голос+стем; собрано без адаптации PTS."
                )
            return self._cmd_stem_voiceduck_mix(
                audio,
                output_path,
                orig_voice_vol=orig_vol,
                dub_volume=dub_vol,
                bg_attenuation_db=self.background_attenuation_db,
            )
        if use_stem_background and mute_original:
            if has_video_stretch:
                return self._cmd_stem_mix_video_adapted(
                    audio, output_path, dub_volume=dub_vol,
                    bg_attenuation_db=self.background_attenuation_db,
                )
            return self._cmd_stem_mix(
                audio, output_path, dub_volume=dub_vol,
                bg_attenuation_db=self.background_attenuation_db,
            )
        if mute_original:
            if has_video_stretch:
                return self._cmd_replace_video_adapted(audio, output_path, dub_volume=dub_vol)
            return self._cmd_replace(audio, output_path, dub_volume=dub_vol)
        return self._cmd_mix(audio, output_path, orig_vol, dub_vol)

    def _cmd_replace_video_adapted(
        self,
        audio: str,
        out: str,
        dub_volume: float = 1.0,
    ) -> list[str]:
        """
        Replace audio + slow video at overflow segments.
        Falls back to normal _cmd_replace when filter cannot be built.
        """
        duration = self._get_duration()
        vf = self._build_video_setpts_filter(self.video_stretch_segments, duration or 9999.0)
        if not vf:
            return self._cmd_replace(audio, out, dub_volume=dub_volume)

        dub_v = max(0.0, min(2.0, dub_volume))
        af_parts: list[str] = []
        if abs(dub_v - 1.0) >= 0.001:
            af_parts.append(f"volume={dub_v:.2f}")
        if duration > 0:
            af_parts.append(f"apad=whole_dur={duration:.3f}")
        af_parts.append(_LOUDNORM_FILTER)

        # Combine video filter with audio processing
        full_filter = vf
        full_filter += f";[1:a]{','.join(af_parts)}[aout]"
        amap = "[aout]"

        cmd = [
            self._ffmpeg, "-y",
            "-i", self.video_path,
            "-i", audio,
            "-filter_complex", full_filter,
            "-map", "[vout]",
            "-map", amap,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
        ]
        if duration > 0:
            cmd.extend(["-t", f"{duration:.3f}"])
        cmd.append(out)
        return cmd

    # ─── Standard commands ────────────────────────────────────────────────────

    def _cmd_replace(self, audio: str, out: str, dub_volume: float = 1.0) -> list[str]:
        """Полная замена аудио: видео + dub на всю длительность, оригинал отключён."""
        dub_v = max(0.0, min(2.0, dub_volume))
        duration = self._get_duration()
        af_parts: list[str] = []
        if abs(dub_v - 1.0) >= 0.001:
            af_parts.append(f"volume={dub_v:.2f}")
        if duration > 0:
            af_parts.append(f"apad=whole_dur={duration:.3f}")
        # EBU R128 loudness normalization — equalises volume across all segments
        af_parts.append(_LOUDNORM_FILTER)
        cmd = [
            self._ffmpeg,
            "-y",
            "-i",
            self.video_path,
            "-i",
            audio,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "-0:a",
            "-c:v",
            "copy",
        ]
        if af_parts:
            cmd.extend(["-af", ",".join(af_parts)])
        if duration > 0:
            cmd.extend(["-t", f"{duration:.3f}"])
        cmd.extend(["-c:a", "aac", "-b:a", "192k", out])
        return cmd

    def _build_ducking_filter(self) -> str:
        """
        Build an FFmpeg volume automation filter that ducks the background
        audio (original track) during dubbed speech intervals.

        The filter uses 'volume' with enable expression to lower background
        level during speech, with smooth fade_in / fade_out transitions via
        'afade' cannot be easily stacked for multiple segments, so we use
        a series of 'volume=enable=...' segments combined in the audio chain.

        For simplicity and reliability, we output a single volume filter that
        keeps level at 1.0 outside speech and at the ducked level during speech.
        Edge-TTS + FFmpeg combo: accurate to ±50ms which is perceptually fine.
        """
        if not self.speech_intervals or not self.ducking_enabled:
            return ""

        ducked_gain = 10 ** (self.ducking_db / 20)  # convert dB to linear gain
        fade_in = self.ducking_fade_in_ms
        fade_out = self.ducking_fade_out_ms

        # Build enable expression for volume filter
        # format: between(t, start, end) for each interval
        conditions = []
        for iv in self.speech_intervals:
            s = max(0.0, (iv.get("start_ms", 0) - fade_in * 1000) / 1000.0)
            e = (iv.get("end_ms", 0) + fade_out * 1000) / 1000.0
            if e > s:
                conditions.append(f"between(t,{s:.3f},{e:.3f})")

        if not conditions:
            return ""

        enable_expr = "+".join(conditions)
        # volume=1 outside speech, ducked_gain during speech
        return (
            f"volume=enable='{enable_expr}':volume={ducked_gain:.4f}:"
            f"eval=frame"
        )

    def _cmd_mix(
        self, audio: str, out: str, orig_vol: float, dub_vol: float
    ) -> list[str]:
        """Микширование: приглушённый оригинал + дубляж на всю длительность видео.

        Без разделения дорожек оригинал = голос+музыка целиком. Чтобы между
        репликами всегда был слышен исходный звук (TZ Task 2/3), а во время дубляжа
        оригинал плавно приглушался и затем возвращался (Task 6/8), используем
        интеллектуальный sidechain-ducking по огибающей дубляжа вместо статичной
        громкости или жёстких переключений.
        """
        orig = max(0.0, min(1.0, orig_vol))
        dub = max(0.0, min(2.0, dub_vol))
        duration = self._get_duration()
        cfg = self._resolve_mix_config(orig, dub)
        dur_pad = f",apad=whole_dur={duration:.3f}" if duration > 0 else ""

        if cfg.ducking_enabled and orig > 0.001:
            dub_pre = f"[1:a]volume={dub:.3f}{dur_pad},{_LOUDNORM_FILTER},{self._SC_FMT}[dubp]"
            split = "[dubp]asplit=2[dubmix][dubkey]"
            orig_pre = f"[0:a]volume={orig:.3f},{self._SC_FMT}[origp]"
            orig_duck = f"[origp][dubkey]{self._sidechain_duck_expr(cfg)}[origd]"
            amix = (
                "[origd][dubmix]"
                "amix=inputs=2:duration=first:dropout_transition=2:normalize=0[out]"
            )
            filt = ";".join([dub_pre, split, orig_pre, orig_duck, amix])
        else:
            dub_chain = f"[1:a]volume={dub:.3f}{dur_pad},{_LOUDNORM_FILTER}[new]"
            # Legacy interval-based ducking, if explicitly configured.
            ducking = self._build_ducking_filter()
            orig_chain = f"[0:a]volume={orig:.3f}"
            if ducking:
                orig_chain += f",{ducking}"
            orig_chain += "[orig]"
            filt = (
                f"{orig_chain};{dub_chain};"
                "[orig][new]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[out]"
            )
        cmd = [
            self._ffmpeg,
            "-y",
            "-i",
            self.video_path,
            "-i",
            audio,
            "-filter_complex",
            filt,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-map",
            "0:v:0",
            "-map",
            "[out]",
        ]
        if duration > 0:
            cmd.extend(["-t", f"{duration:.3f}"])
        cmd.append(out)
        return cmd

    def _has_background_stem(self) -> bool:
        return bool(
            self.background_audio_path
            and Path(self.background_audio_path).is_file()
        )

    def _has_dialogue_stem(self) -> bool:
        return bool(
            self.dialogue_audio_path
            and Path(self.dialogue_audio_path).is_file()
        )

    # ── Professional intelligent voice ducking ────────────────────────────────

    def _resolve_mix_config(
        self, orig_vol: float, dub_vol: float
    ) -> "AudioMixConfig":
        """Return the resolved mix policy, or a sane default derived from levels."""
        if self.mix_config is not None:
            return self.mix_config
        from engines.audio_mix_config import AudioMixConfig, is_voice_ducking_enabled

        return AudioMixConfig(
            dub_volume=dub_vol,
            original_voice_volume=orig_vol,
            ducking_enabled=is_voice_ducking_enabled(),
        )

    @staticmethod
    def _sidechain_duck_expr(cfg: "AudioMixConfig") -> str:
        """
        FFmpeg ``sidechaincompress`` string that ducks the *main* input whenever
        the *sidechain* (dub) is speaking.  Between dubbed lines the compressor
        releases and the main track (original voice) returns to full level, with
        smooth attack/release — no abrupt jumps (TZ Task 6/8/10).
        """
        p = cfg.sidechain_params()
        return (
            f"sidechaincompress=threshold={p['threshold']:.4f}:"
            f"ratio={p['ratio']:.2f}:attack={p['attack']:.0f}:"
            f"release={p['release']:.0f}:makeup={p['makeup']:.2f}"
        )

    # Common audio format so sidechaincompress/amix inputs are compatible.
    _SC_FMT = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"

    def _cmd_stem_voiceduck_mix(
        self,
        dub_audio: str,
        out: str,
        orig_voice_vol: float,
        dub_volume: float = 1.0,
        bg_attenuation_db: float = 4.5,
    ) -> list[str]:
        """
        Final professional mix from separated stems:
          input0 = video
          input1 = accompaniment stem (music + SFX + ambience) — stays fully alive
          input2 = isolated original voice (vocals) — underlaid + ducked by the dub
          input3 = dubbed (translated) speech

        Result: music/effects unchanged throughout; the original voice is audible
        but automatically ducked while the dubbed line plays and restored between
        lines; the dub is always intelligible.
        """
        orig_v = max(0.0, min(1.0, orig_voice_vol))
        dub_v = max(0.0, min(2.0, dub_volume))
        bg_gain = 10 ** (-max(0.0, bg_attenuation_db) / 20.0)
        duration = self._get_duration()
        cfg = self._resolve_mix_config(orig_v, dub_v)
        bg_vol = bg_gain * cfg.background_volume

        dur_pad = f",apad=whole_dur={duration:.3f}" if duration > 0 else ""

        dub_pre = (
            f"[3:a]volume={dub_v:.3f}{dur_pad},{_LOUDNORM_FILTER},{self._SC_FMT}[dubp]"
        )
        split = "[dubp]asplit=2[dubmix][dubkey]"
        voice_pre = f"[2:a]volume={orig_v:.3f},{self._SC_FMT}[voice]"
        bg_pre = f"[1:a]volume={bg_vol:.4f}{dur_pad},{self._SC_FMT}[bg]"

        if cfg.ducking_enabled and orig_v > 0.001:
            voice_duck = f"[voice][dubkey]{self._sidechain_duck_expr(cfg)}[voiced]"
            amix = (
                "[bg][voiced][dubmix]"
                "amix=inputs=3:duration=first:dropout_transition=0:normalize=0[out]"
            )
            filt = ";".join([dub_pre, split, voice_pre, bg_pre, voice_duck, amix])
        else:
            # Ducking off → static underlay of the original voice.
            voice_pre2 = f"[2:a]volume={orig_v:.3f},{self._SC_FMT}[voiced]"
            dub_pre2 = (
                f"[3:a]volume={dub_v:.3f}{dur_pad},{_LOUDNORM_FILTER},{self._SC_FMT}[dubmix]"
            )
            amix = (
                "[bg][voiced][dubmix]"
                "amix=inputs=3:duration=first:dropout_transition=0:normalize=0[out]"
            )
            filt = ";".join([dub_pre2, voice_pre2, bg_pre, amix])

        cmd = [
            self._ffmpeg, "-y",
            "-i", self.video_path,
            "-i", self.background_audio_path,
            "-i", self.dialogue_audio_path,
            "-i", dub_audio,
            "-filter_complex", filt,
            "-map", "0:v:0",
            "-map", "[out]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
        ]
        if duration > 0:
            cmd.extend(["-t", f"{duration:.3f}"])
        cmd.append(out)
        return cmd

    def _cmd_stem_mix(
        self,
        dub_audio: str,
        out: str,
        dub_volume: float = 1.0,
        bg_attenuation_db: float = 4.5,
    ) -> list[str]:
        """
        Final mix: video + music/SFX stem (attenuated) + dub TTS.
        Original video speech is not used.
        """
        dub_v = max(0.0, min(2.0, dub_volume))
        bg_gain = 10 ** (-max(0.0, bg_attenuation_db) / 20.0)
        duration = self._get_duration()

        bg_chain = f"[1:a]volume={bg_gain:.4f}"
        if duration > 0:
            bg_chain += f",apad=whole_dur={duration:.3f}"
        bg_chain += "[bg]"

        dub_chain = f"[2:a]volume={dub_v:.3f}"
        if duration > 0:
            dub_chain += f",apad=whole_dur={duration:.3f}"
        dub_chain += f",{_LOUDNORM_FILTER}[dub]"

        filt = (
            f"{bg_chain};{dub_chain};"
            f"[bg][dub]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[out]"
        )

        cmd = [
            self._ffmpeg,
            "-y",
            "-i",
            self.video_path,
            "-i",
            self.background_audio_path,
            "-i",
            dub_audio,
            "-filter_complex",
            filt,
            "-map",
            "0:v:0",
            "-map",
            "[out]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
        if duration > 0:
            cmd.extend(["-t", f"{duration:.3f}"])
        cmd.append(out)
        return cmd

    def _cmd_stem_mix_video_adapted(
        self,
        dub_audio: str,
        out: str,
        dub_volume: float = 1.0,
        bg_attenuation_db: float = 4.5,
    ) -> list[str]:
        """Stem mix with slowed video segments at overflow points."""
        duration = self._get_duration()
        vf = self._build_video_setpts_filter(self.video_stretch_segments, duration or 9999.0)
        if not vf:
            return self._cmd_stem_mix(
                dub_audio,
                out,
                dub_volume=dub_volume,
                bg_attenuation_db=bg_attenuation_db,
            )

        dub_v = max(0.0, min(2.0, dub_volume))
        bg_gain = 10 ** (-max(0.0, bg_attenuation_db) / 20.0)

        bg_chain = f"[1:a]volume={bg_gain:.4f}"
        if duration > 0:
            bg_chain += f",apad=whole_dur={duration:.3f}"
        bg_chain += "[bg]"

        dub_chain = f"[2:a]volume={dub_v:.3f}"
        if duration > 0:
            dub_chain += f",apad=whole_dur={duration:.3f}"
        dub_chain += f",{_LOUDNORM_FILTER}[dub]"

        audio_mix = (
            f"{bg_chain};{dub_chain};"
            f"[bg][dub]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        full_filter = vf + ";" + audio_mix

        cmd = [
            self._ffmpeg,
            "-y",
            "-i",
            self.video_path,
            "-i",
            self.background_audio_path,
            "-i",
            dub_audio,
            "-filter_complex",
            full_filter,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
        if duration > 0:
            cmd.extend(["-t", f"{duration:.3f}"])
        cmd.append(out)
        return cmd

    def _audio_path(self) -> str:
        return self.timed_audio or self.audio_path

    def _get_duration(self) -> float:
        probe = self._ffprobe or shutil.which("ffprobe")
        if not probe or not self.video_path:
            return 0.0
        try:
            result = subprocess.run(
                [
                    probe,
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    self.video_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return float(result.stdout.strip())
        except Exception:
            return 0.0


def mux_keep_original_audio(
    video_path: str,
    output_path: str,
    timeout_sec: int = 600,
) -> tuple[bool, str, list[str]]:
    """Копирует видео с оригинальной дорожкой (режим «только субтитры»)."""
    from engines.ffmpeg_paths import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg or not Path(video_path).exists():
        return False, "", ["FFmpeg или видеофайл недоступны"]

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        video_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        output_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            return False, "", [proc.stderr[-500:] if proc.stderr else "ffmpeg failed"]
        if not Path(output_path).exists():
            return False, "", ["Выходной файл не создан"]
        return True, output_path, []
    except Exception as e:
        return False, "", [str(e)]
