"""
Post-mux video integrity checks for TubeDub (developer diagnostics).
Ensures: original duration, no re-encode (copy), stream presence.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _probe(path: str) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path or not Path(path).exists():
        return {}

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout or "{}")
    except Exception:
        return {}


def _stream(probe: dict[str, Any], codec_type: str) -> dict[str, Any] | None:
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") == codec_type:
            return stream
    return None


def _duration(probe: dict[str, Any]) -> float:
    fmt = probe.get("format") or {}
    try:
        return float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fps(stream: dict[str, Any] | None) -> float | None:
    if not stream:
        return None
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or ""
    if not rate or rate == "0/0":
        return None
    if "/" in str(rate):
        num, den = str(rate).split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d else None
        except ValueError:
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def _frame_count(stream: dict[str, Any] | None) -> int | None:
    if not stream:
        return None
    nb = stream.get("nb_frames")
    if nb is not None:
        try:
            return int(nb)
        except (TypeError, ValueError):
            pass
    return None


def verify_video_integrity(source_path: str, output_path: str) -> dict[str, Any]:
    """
    Compare source and dubbed MP4.
    Returns report dict suitable for dev_diagnostics.log_video_integrity().
    """
    report: dict[str, Any] = {
        "ok": False,
        "source": source_path,
        "output": output_path,
        "checks": {},
        "warnings": [],
        "errors": [],
    }

    if not Path(source_path).exists():
        report["errors"].append(f"source missing: {source_path}")
        return report
    if not Path(output_path).exists():
        report["errors"].append(f"output missing: {output_path}")
        return report

    src_probe = _probe(source_path)
    out_probe = _probe(output_path)
    if not src_probe:
        report["errors"].append("ffprobe failed on source")
        return report
    if not out_probe:
        report["errors"].append("ffprobe failed on output")
        return report

    src_v = _stream(src_probe, "video")
    out_v = _stream(out_probe, "video")
    src_a = _stream(src_probe, "audio")
    out_a = _stream(out_probe, "audio")

    src_dur = _duration(src_probe)
    out_dur = _duration(out_probe)
    dur_delta = abs(out_dur - src_dur)
    dur_ok = dur_delta <= 0.15
    report["checks"]["duration_match"] = {
        "ok": dur_ok,
        "source_sec": round(src_dur, 3),
        "output_sec": round(out_dur, 3),
        "delta_sec": round(dur_delta, 3),
        "max_allowed_delta_sec": 0.15,
    }
    if not dur_ok:
        report["warnings"].append(
            f"Duration drift {dur_delta:.3f}s exceeds 150ms tolerance"
        )

    src_fps = _fps(src_v)
    out_fps = _fps(out_v)
    fps_ok = True
    if src_fps and out_fps:
        fps_ok = abs(src_fps - out_fps) < 0.05
    report["checks"]["fps_preserved"] = {
        "ok": fps_ok,
        "source_fps": src_fps,
        "output_fps": out_fps,
    }
    if not fps_ok:
        report["warnings"].append(f"FPS changed: {src_fps} -> {out_fps}")

    src_frames = _frame_count(src_v)
    out_frames = _frame_count(out_v)
    frames_ok = True
    if src_frames is not None and out_frames is not None:
        frames_ok = src_frames == out_frames
        report["checks"]["frame_count"] = {
            "ok": frames_ok,
            "source": src_frames,
            "output": out_frames,
        }
        if not frames_ok:
            report["warnings"].append(f"Frame count changed: {src_frames} -> {out_frames}")
    else:
        report["checks"]["frame_count"] = {
            "ok": None,
            "note": "nb_frames unavailable — duration/fps used instead",
        }

    video_present = out_v is not None
    report["checks"]["video_stream_present"] = {"ok": video_present}
    if not video_present:
        report["errors"].append("Output has no video stream")

    codec_ok = True
    if src_v and out_v:
        same_codec = src_v.get("codec_name") == out_v.get("codec_name")
        codec_ok = same_codec
        report["checks"]["video_codec_copy"] = {
            "ok": same_codec,
            "source_codec": src_v.get("codec_name"),
            "output_codec": out_v.get("codec_name"),
        }
        if not same_codec:
            report["warnings"].append(
                f"Video re-encoded or codec changed: "
                f"{src_v.get('codec_name')} -> {out_v.get('codec_name')}"
            )

    width_ok = height_ok = True
    if src_v and out_v:
        width_ok = src_v.get("width") == out_v.get("width")
        height_ok = src_v.get("height") == out_v.get("height")
        report["checks"]["resolution"] = {
            "ok": width_ok and height_ok,
            "source": [src_v.get("width"), src_v.get("height")],
            "output": [out_v.get("width"), out_v.get("height")],
        }

    dub_audio_ok = out_a is not None
    report["checks"]["dub_audio_present"] = {"ok": dub_audio_ok}
    if not dub_audio_ok:
        report["errors"].append("Output has no audio stream")

    report["checks"]["no_shortest_truncation"] = {
        "ok": out_dur >= src_dur - 0.15,
        "note": "output duration must not be shorter than source (no -shortest cut)",
    }

    hard_fail = bool(report["errors"])
    soft_fail = any(
        isinstance(v, dict) and v.get("ok") is False
        for v in report["checks"].values()
    )
    report["ok"] = not hard_fail and not soft_fail
    return report
