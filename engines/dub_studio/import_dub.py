"""Import dub task / review JSON into Dub Studio project."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from engines.dub_studio.emotion import apply_emotion_bridge, extract_emotion
from engines.dub_studio.models import DubProject, SegmentVersion, StudioSegment
from engines.dub_studio.store import ProjectStore
from engines.dub_studio.timing import apply_hard_anchor, update_segment_timing


def import_from_review_json(
    app_dir: Path,
    review_path: Path,
    *,
    title: str = "",
    original_audio: Path | None = None,
) -> DubProject:
    store = ProjectStore(app_dir)
    raw = json.loads(review_path.read_text(encoding="utf-8"))
    segments_raw = raw.get("segments") or raw.get("items") or []
    project = store.create_empty(title=title or review_path.stem)

    segments: list[StudioSegment] = []
    for i, row in enumerate(segments_raw):
        start = int(row.get("start_ms") or row.get("start") or 0)
        end = int(row.get("end_ms") or row.get("end") or start + 3000)
        text = str(row.get("final_text") or row.get("naturalized_text") or row.get("text") or "")
        sid = str(row.get("segment_id") or row.get("id") or uuid.uuid4())
        seg = StudioSegment(
            segment_id=sid,
            index=i,
            text=text,
            start_ms=start,
            end_ms=end,
        )
        apply_hard_anchor(seg)
        tts_file = row.get("tts_file") or row.get("file")
        tts_ms = int(row.get("tts_ms") or row.get("duration_ms") or 0)
        if tts_file:
            p = Path(str(tts_file))
            if not p.is_absolute():
                p = app_dir / "output" / p.name
            if p.is_file() and not tts_ms:
                try:
                    from pydub import AudioSegment

                    tts_ms = len(AudioSegment.from_file(str(p)))
                except Exception:
                    tts_ms = 0
            ver = SegmentVersion(
                version_id=str(uuid.uuid4()),
                label="A",
                audio_path=str(p) if p.is_file() else "",
                source="tts",
                created_ms=int(time.time() * 1000),
            )
            seg.versions = [ver]
            seg.active_version_id = ver.version_id

        update_segment_timing(seg, tts_ms=tts_ms or max(0, end - start))
        emo = extract_emotion(original_audio, start_ms=start, end_ms=end)
        seg.emotion = str(emo.get("emotion") or "NEUTRAL")
        seg.emotion_confidence = float(emo.get("confidence") or 0)
        seg.tts_params = apply_emotion_bridge({}, seg.emotion).get("tts_params", {})
        segments.append(seg)

    project.segments = segments
    project.duration_ms = max((s.end_ms for s in segments), default=0)
    project.meta["imported_from"] = str(review_path)
    return store.save(project)
