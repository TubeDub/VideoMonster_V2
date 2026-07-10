"""Версия TubeDub — единый источник для UI, обновлений и отчётов."""

from __future__ import annotations

APP_NAME = "TubeDub"
APP_VERSION = "2.0.0"
APP_CHANNEL = "beta"


def version_info() -> dict:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "channel": APP_CHANNEL,
        "display": f"{APP_NAME} {APP_VERSION} ({APP_CHANNEL})",
    }
