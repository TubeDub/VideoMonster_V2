"""Feature Flags — safe module loading and developer isolation."""

from engines.feature_flags.dev_log import DeveloperLog, get_dev_log
from engines.feature_flags.loader import safe_call, safe_import
from engines.feature_flags.manager import FeatureManager, get_feature_manager, require_feature
from engines.feature_flags.modes import UserMode, normalize_mode, visible_for_mode

__all__ = [
    "DeveloperLog",
    "FeatureManager",
    "UserMode",
    "get_dev_log",
    "get_feature_manager",
    "normalize_mode",
    "require_feature",
    "safe_call",
    "safe_import",
    "visible_for_mode",
]
