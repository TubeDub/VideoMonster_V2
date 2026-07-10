"""Professional Dubbing — natural TTS prosody (independent module)."""

from engines.professional_dubbing.config import is_enabled, is_prosody_style
from engines.professional_dubbing.prepare import prepare_tts_groups_prosody
from engines.professional_dubbing.prosody import ProsodyPlan, build_prosody_plan

__all__ = [
    "is_enabled",
    "is_prosody_style",
    "ProsodyPlan",
    "build_prosody_plan",
    "prepare_tts_groups_prosody",
]
