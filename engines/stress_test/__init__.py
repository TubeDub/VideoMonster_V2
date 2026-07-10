"""Stress Test Center — autonomous quality testing for TubeDub."""

from engines.stress_test.config import is_module_available
from engines.stress_test.guards import allow_stress_test

__all__ = ["allow_stress_test", "is_module_available"]
