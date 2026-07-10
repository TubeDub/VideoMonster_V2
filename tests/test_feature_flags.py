"""Tests for engines.core.feature_flags."""

from __future__ import annotations

import os
from pathlib import Path


def test_is_developer_env():
    from engines.core.feature_flags import is_developer

    os.environ["VM_DEV_MODE"] = "1"
    assert is_developer()
    os.environ.pop("VM_DEV_MODE", None)


def test_is_enabled_core():
    from engines.core.feature_flags import is_enabled

    assert is_enabled("core", developer_session=True)


def test_module_visible_home():
    from engines.core.feature_flags import is_module_visible

    assert is_module_visible("home", developer_session=False, user_mode="basic")
