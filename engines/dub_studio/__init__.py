"""Dub Studio — professional multitrack dubbing editor."""

from engines.dub_studio.config import dub_studio_enabled, require_dub_studio
from engines.dub_studio.service import DubStudioService, get_dub_studio_service

__all__ = [
    "DubStudioService",
    "dub_studio_enabled",
    "get_dub_studio_service",
    "require_dub_studio",
]
