"""AutoDub — automatic dubbing pipeline (AI Core only).

AutoDub runs the full AI Core chain and produces a Project Package.
Dub Studio is a separate environment that opens the package for manual editing.
"""

from engines.autodub.project_package import build_autodub_project_package

__all__ = ["build_autodub_project_package"]
