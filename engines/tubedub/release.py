"""Release channels — single flag controls module visibility."""

from __future__ import annotations

from enum import Enum


class ReleaseChannel(str, Enum):
    DISABLED = "DISABLED"
    DEVELOPER = "DEVELOPER"
    RELEASE = "RELEASE"


def parse_release_channel(raw: str | None) -> ReleaseChannel:
    v = (raw or ReleaseChannel.DISABLED.value).strip().upper()
    if v == ReleaseChannel.RELEASE.value:
        return ReleaseChannel.RELEASE
    if v == ReleaseChannel.DEVELOPER.value:
        return ReleaseChannel.DEVELOPER
    return ReleaseChannel.DISABLED


def channel_visible(
    channel: ReleaseChannel,
    *,
    developer_session: bool,
    user_mode: str = "basic",
) -> bool:
    if channel == ReleaseChannel.DISABLED:
        return False
    if channel == ReleaseChannel.DEVELOPER:
        return developer_session and user_mode == "developer"
    return True
