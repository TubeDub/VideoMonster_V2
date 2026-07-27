"""Offline MT readiness must not claim ready without runtime deps."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_marian_not_ready_without_torch(tmp_path: Path):
    from engines.model_manager.downloader import is_mt_engine_ready

    with (
        patch("engines.model_manager.downloader._has_package", return_value=False),
        patch(
            "engines.model_manager.downloader.verify_hf_model",
            return_value=True,
        ),
    ):
        assert is_mt_engine_ready(tmp_path, "marian", "en", "ru") is False


def test_marian_ready_when_runtime_and_weights(tmp_path: Path):
    from engines.model_manager.downloader import is_mt_engine_ready

    with (
        patch("engines.model_manager.downloader._has_package", return_value=True),
        patch(
            "engines.model_manager.downloader.verify_hf_model",
            return_value=True,
        ),
    ):
        assert is_mt_engine_ready(tmp_path, "marian", "en", "ru") is True
