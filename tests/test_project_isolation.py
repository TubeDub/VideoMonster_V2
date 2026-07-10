"""
Test §15: Project Isolation.

Verifies that two sequential dubbing projects do NOT share:
  • segment text
  • timing data
  • audio file references
  • task state
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pathlib
import uuid


# ── ProjectSession isolation tests ────────────────────────────────────────────

class TestProjectSession:
    def _make_dir(self, tmp_path):
        d = pathlib.Path(tmp_path) / "output"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_two_sessions_have_different_dirs(self, tmp_path):
        from engines.dubbing_engine.project_session import create_session
        out = self._make_dir(tmp_path)
        s1 = create_session(uuid.uuid4().hex, out, "movie")
        s2 = create_session(uuid.uuid4().hex, out, "blogger")
        assert s1.session_dir != s2.session_dir

    def test_session_data_isolated(self, tmp_path):
        from engines.dubbing_engine.project_session import create_session
        out = self._make_dir(tmp_path)
        s1 = create_session(uuid.uuid4().hex, out, "movie")
        s2 = create_session(uuid.uuid4().hex, out, "blogger")
        s1.set("segments", ["Hello world"])
        s2.set("segments", ["Привіт світ"])
        assert s1.get("segments") != s2.get("segments")
        assert s2.get("segments") == ["Привіт світ"]

    def test_session_cleanup_removes_tracked_files(self, tmp_path):
        from engines.dubbing_engine.project_session import create_session
        out = self._make_dir(tmp_path)
        s = create_session(uuid.uuid4().hex, out, "movie")
        # Create a temp file tracked by the session
        p = s.session_path("temp_audio.mp3")
        p.write_text("fake audio data")
        assert p.exists()
        removed = s.cleanup(keep_output=False)
        assert removed >= 1
        assert not p.exists()

    def test_finish_session_marks_finished(self, tmp_path):
        from engines.dubbing_engine.project_session import create_session, finish_session
        out = self._make_dir(tmp_path)
        tid = uuid.uuid4().hex
        s = create_session(tid, out, "movie")
        assert s.finished_at == 0.0
        finish_session(tid)
        assert s.finished_at > 0.0

    def test_get_session_returns_correct_session(self, tmp_path):
        from engines.dubbing_engine.project_session import create_session, get_session
        out = self._make_dir(tmp_path)
        tid1, tid2 = uuid.uuid4().hex, uuid.uuid4().hex
        s1 = create_session(tid1, out, "movie")
        s2 = create_session(tid2, out, "blogger")
        assert get_session(tid1) is s1
        assert get_session(tid2) is s2
        assert get_session(tid1) is not s2

    def test_cleanup_session_removes_from_registry(self, tmp_path):
        from engines.dubbing_engine.project_session import (
            create_session, get_session, cleanup_session
        )
        out = self._make_dir(tmp_path)
        tid = uuid.uuid4().hex
        create_session(tid, out, "movie")
        assert get_session(tid) is not None
        cleanup_session(tid, keep_output=True)
        assert get_session(tid) is None

    def test_session_summary_has_required_fields(self, tmp_path):
        from engines.dubbing_engine.project_session import create_session
        out = self._make_dir(tmp_path)
        tid = uuid.uuid4().hex
        s = create_session(tid, out, "movie")
        summary = s.summary()
        for key in ("session_id", "task_id", "content_mode", "created_at"):
            assert key in summary

    def test_old_session_data_never_in_new_session(self, tmp_path):
        from engines.dubbing_engine.project_session import create_session
        out = self._make_dir(tmp_path)
        tid1 = uuid.uuid4().hex
        tid2 = uuid.uuid4().hex
        s1 = create_session(tid1, out, "movie")
        s1.set("text", "Film dialogue line 1")
        s1.set("timing", [{"start": 0, "end": 1000}])

        # Simulate starting a new project
        s2 = create_session(tid2, out, "blogger")
        # s2 must not have any data from s1
        assert s2.get("text") is None
        assert s2.get("timing") is None

    def test_multiple_sessions_independent_file_tracking(self, tmp_path):
        from engines.dubbing_engine.project_session import create_session
        out = self._make_dir(tmp_path)
        s1 = create_session(uuid.uuid4().hex, out, "movie")
        s2 = create_session(uuid.uuid4().hex, out, "blogger")
        p1 = s1.session_path("seg_0.mp3")
        p2 = s2.session_path("seg_0.mp3")
        # Even same filename → different paths (different session dirs)
        assert p1 != p2
        assert p1.parent != p2.parent


# ── Content Mode isolation ─────────────────────────────────────────────────────

class TestContentModeIsolation:
    def test_movie_and_blogger_have_different_profiles(self):
        from engines.dubbing_engine.content_mode import get_profile, ContentMode
        movie = get_profile(ContentMode.MOVIE)
        blogger = get_profile(ContentMode.BLOGGER)
        assert movie.slot_tolerance_pct < blogger.slot_tolerance_pct
        assert movie.strict_pause_preservation == True
        assert blogger.strict_pause_preservation == False

    def test_engine_respects_movie_atempo(self):
        from engines.dubbing_engine import DubbingEngine
        engine = DubbingEngine(lang="uk", content_mode="movie")
        assert engine._max_atempo <= 1.13  # movie mode is strict

    def test_engine_respects_blogger_tolerance(self):
        from engines.dubbing_engine import DubbingEngine
        engine = DubbingEngine(lang="uk", content_mode="blogger")
        assert engine._slot_tolerance_pct >= 15.0

    def test_engine_respects_interview_no_merge(self):
        from engines.dubbing_engine import DubbingEngine
        engine = DubbingEngine(lang="uk", content_mode="interview")
        assert engine._allow_merge == False

    def test_podcast_has_high_tolerance(self):
        from engines.dubbing_engine import DubbingEngine
        engine = DubbingEngine(lang="uk", content_mode="podcast")
        assert engine._slot_tolerance_pct >= 20.0

    def test_content_mode_from_str_invalid_defaults_to_movie(self):
        from engines.dubbing_engine.content_mode import ContentMode
        mode = ContentMode.from_str("invalid_xyz")
        assert mode == ContentMode.MOVIE

    def test_all_modes_for_ui_returns_all_9(self):
        from engines.dubbing_engine.content_mode import all_modes_for_ui
        modes = all_modes_for_ui("ru")
        assert len(modes) == 9
        values = {m["value"] for m in modes}
        expected = {"movie", "tv_series", "anime", "cartoon", "youtube",
                    "blogger", "podcast", "interview", "audiobook"}
        assert values == expected

    def test_auto_detect_podcast(self):
        from engines.dubbing_engine.content_mode import auto_detect_mode, ContentMode
        mode = auto_detect_mode(
            avg_segment_ms=10000,
            avg_gap_ms=2500,
            segment_count=50,
        )
        assert mode == ContentMode.PODCAST

    def test_auto_detect_blogger(self):
        from engines.dubbing_engine.content_mode import auto_detect_mode, ContentMode
        mode = auto_detect_mode(
            avg_gap_ms=300,
            segment_count=60,
            avg_segment_ms=3000,
        )
        assert mode == ContentMode.BLOGGER


# ── Segment Log tests ─────────────────────────────────────────────────────────

class TestSegmentLog:
    def test_log_write_creates_tsv(self, tmp_path):
        from engines.dubbing_engine.segment_log import SegmentLog, SegmentLogEntry
        log = SegmentLog(task_id="test001", app_dir=pathlib.Path(tmp_path))
        log.add(SegmentLogEntry(
            segment_id=0,
            original_text="He went outside.",
            translation="Він вийшов надвір.",
            adapted_text="Він вийшов.",
            text_sent_to_tts="Він вийшов.",
            predicted_duration_ms=1400,
            final_duration_ms=1350,
            strategy="adapted",
            merge_status="standalone",
            pause_duration_ms=160,
            validation_passed=True,
        ))
        path = log.write()
        assert path is not None
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "segment_id" in content
        assert "He went outside." in content
        assert "Він вийшов." in content

    def test_log_summary_counts(self, tmp_path):
        from engines.dubbing_engine.segment_log import SegmentLog, SegmentLogEntry
        log = SegmentLog(task_id="test002", app_dir=pathlib.Path(tmp_path))
        for i in range(3):
            log.add(SegmentLogEntry(segment_id=i, strategy="direct", validation_passed=True))
        log.add(SegmentLogEntry(segment_id=3, strategy="adapted", validation_passed=True))
        log.add(SegmentLogEntry(segment_id=4, strategy="skip_tts", validation_passed=False))
        summary = log.summary()
        assert summary["total"] == 5
        assert summary["adapted"] == 1
        assert summary["skipped"] == 1
        assert summary["strategies"]["direct"] == 3
